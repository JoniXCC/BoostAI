from boostai.actions.action_models import ActionRequest, Approval, ResultStatus
from boostai.actions.rollback import RollbackManager
from boostai.core.models import PowerPlan, StartupItem, StartupSource
from boostai.knowledge.startup_catalog import StartupCategory, assess
from boostai.metrics.startup import disabled_bytes, extract_target, is_enabled_state
from tests.fakes import proc


def run(executor, action_id, **params):
    r = ActionRequest(action_id=action_id, params=params)
    return executor.execute(r, Approval(r.request_id), session_id="s1")


def spotify(ops, raw=None):
    item = StartupItem("HKCU_RUN:Spotify", "Spotify", '"C:\\Users\\a\\AppData\\Roaming\\Spotify\\Spotify.exe" /minimized',
                       StartupSource.HKCU_RUN, True, "C:\\Users\\a\\AppData\\Roaming\\Spotify\\Spotify.exe", "Spotify AB")
    ops.startup_items = [item]
    if raw is not None:
        ops.startup_state[("HKCU_RUN", "Spotify")] = raw
    return item


def test_startup_state_bytes():
    assert is_enabled_state(None)
    assert is_enabled_state(bytes([2] + [0] * 11))
    assert not is_enabled_state(bytes([3] + [0] * 11))
    b = disabled_bytes(0)
    assert len(b) == 12 and b[0] == 3


def test_extract_target_variants():
    assert extract_target('"C:\\A B\\x.exe" -silent') == "C:\\A B\\x.exe"
    assert extract_target("C:\\Tools\\y.exe --flag") == "C:\\Tools\\y.exe"
    assert extract_target("") is None


def test_startup_categories():
    base = dict(command="", source=StartupSource.HKCU_RUN, enabled=True)
    assert assess(StartupItem("HKLM_RUN:SecurityHealth", "SecurityHealth", target_path="C:\\Windows\\system32\\SecurityHealthSystray.exe", **base)).category == StartupCategory.PROTECTED
    assert assess(StartupItem("x:RtkAudUService", "RtkAudUService", publisher="Realtek Semiconductor", **base)).category == StartupCategory.RECOMMENDED_KEEP
    assert assess(StartupItem("x:Discord", "Discord", **base)).category == StartupCategory.HIGH_IMPACT
    assert assess(StartupItem("x:Telegram", "Telegram Desktop", **base)).category == StartupCategory.OPTIONAL
    assert assess(StartupItem("x:Zzq", "zzq_helper", **base)).category == StartupCategory.UNKNOWN


def test_measured_memory_upgrades_optional_to_high_impact():
    item = StartupItem("x:Telegram", "Telegram", "", StartupSource.HKCU_RUN, True, "C:\\T\\telegram.exe")
    a = assess(item, [proc(1, "Telegram.exe", private=400 * 1024 * 1024)])
    assert a.category == StartupCategory.HIGH_IMPACT and a.impact_basis.startswith("measured")


def test_disable_startup_then_rollback_restores_exact_bytes(executor, ops, repo, ctx, registry):
    original = bytes([2] + [0] * 11)
    spotify(ops, original)
    out = run(executor, "DISABLE_STARTUP_ITEM", item_id="HKCU_RUN:Spotify")
    assert out.status == ResultStatus.SUCCESS
    assert not is_enabled_state(ops.startup_state[("HKCU_RUN", "Spotify")])
    rec = repo.get_action(out.record_id)
    assert rec["rollback_available"] == 1
    assert rec["previous_state"]["raw"] == original.hex()

    rm = RollbackManager(registry, lambda: ctx, repo)
    result = rm.rollback(out.record_id)
    assert result.success
    assert ops.startup_state[("HKCU_RUN", "Spotify")] == original
    # A second undo is refused.
    assert not rm.rollback(out.record_id).success


def test_rollback_of_item_without_prior_value_removes_marker(executor, ops, repo, ctx, registry):
    spotify(ops)  # no StartupApproved value existed
    out = run(executor, "DISABLE_STARTUP_ITEM", item_id="HKCU_RUN:Spotify")
    RollbackManager(registry, lambda: ctx, repo).rollback(out.record_id)
    assert ops.startup_state[("HKCU_RUN", "Spotify")] is None


def test_disable_protected_startup_blocked(executor, ops):
    ops.startup_items = [StartupItem("HKCU_RUN:Defender", "WindowsDefender", "x", StartupSource.HKCU_RUN, True,
                                     "C:\\Windows\\System32\\SecurityHealthSystray.exe")]
    assert run(executor, "DISABLE_STARTUP_ITEM", item_id="HKCU_RUN:Defender").status == ResultStatus.BLOCKED


def test_already_disabled_is_blocked(executor, ops):
    spotify(ops, bytes([3] + [0] * 11))
    assert run(executor, "DISABLE_STARTUP_ITEM", item_id="HKCU_RUN:Spotify").status == ResultStatus.BLOCKED


def test_power_plan_change_and_session_rollback(executor, ops, repo, ctx, registry):
    ops.plans = [PowerPlan("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced", True),
                 PowerPlan("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "High performance", False)]
    out = run(executor, "CHANGE_POWER_PLAN", guid="8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    assert out.status == ResultStatus.SUCCESS
    results = RollbackManager(registry, lambda: ctx, repo).rollback_session("s1")
    assert results and results[0][1].success
    assert next(p for p in ops.plans if p.active).name == "Balanced"


def test_non_reversible_action_cannot_be_rolled_back(executor, repo, ctx, registry):
    out = run(executor, "FLUSH_DNS")
    ok, reason = RollbackManager(registry, lambda: ctx, repo).can_rollback(repo.get_action(out.record_id))
    assert not ok and "not applicable" in reason.lower()
