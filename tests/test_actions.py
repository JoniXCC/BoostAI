"""Whitelist, validation, approval and execution behaviour of the action system."""

from boostai.actions.action_models import ActionRequest, Approval, ResultStatus
from boostai.config.settings import OptimizationMode
from boostai.core.models import MB, PowerPlan
from tests.fakes import proc


def req(action_id, **params):
    return ActionRequest(action_id=action_id, params=params)


def approve(r):
    return Approval(request_id=r.request_id)


def test_every_action_id_has_complete_metadata(registry):
    for d in registry.definitions():
        assert d.description and d.requirements is not None and d.validation_checks
        assert d.execution_method and d.verification_method and d.affected_component
        assert d.risk.value in ("LOW", "MEDIUM", "HIGH")
        if d.reversibility.value in ("REVERSIBLE", "PARTIALLY_REVERSIBLE"):
            assert d.rollback_method


def test_unknown_action_is_rejected(executor):
    r = req("RUN_POWERSHELL", command="Remove-Item C:\\ -Recurse")
    v = executor.validate(r)
    assert not v.ok and v.status == ResultStatus.REJECTED
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.REJECTED


def test_non_string_and_case_variant_ids_rejected(registry):
    assert registry.get(None) is None
    assert registry.get(123) is None
    assert registry.get("restart_process") is None


def test_malformed_params_rejected(executor):
    r = req("FLUSH_DNS", extra="rm -rf")
    assert executor.validate(r).status == ResultStatus.REJECTED
    r2 = req("RESTART_PROCESS", pid="abc", create_time=1, name="x")
    assert executor.validate(r2).status == ResultStatus.REJECTED
    r3 = req("STOP_OPTIONAL_SERVICE", name="WSearch & calc.exe")
    assert executor.validate(r3).status == ResultStatus.REJECTED


def test_execution_requires_matching_approval(executor, ops):
    r = req("FLUSH_DNS")
    assert executor.execute(r, None).status == ResultStatus.REJECTED
    other = req("FLUSH_DNS")
    assert executor.execute(r, approve(other)).status == ResultStatus.REJECTED
    assert ("flush_dns",) not in ops.calls
    assert executor.execute(r, approve(r)).status == ResultStatus.SUCCESS
    assert ("flush_dns",) in ops.calls


def test_safe_mode_blocks_medium_risk(executor, ops, safe_mode):
    p = proc(5000, "discord.exe")
    ops.processes = [p]
    r = req("CLOSE_USER_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name)
    v = executor.validate(r)
    assert not v.ok and v.status == ResultStatus.BLOCKED and "SAFE mode" in v.reasons[0]
    assert executor.execute(r, approve(r)).status == ResultStatus.BLOCKED
    assert ops.calls == []


def test_protected_process_close_is_blocked(executor, ops):
    p = proc(700, "lsass.exe", user="NT AUTHORITY\\SYSTEM", session=0, exe="C:\\Windows\\System32\\lsass.exe")
    ops.processes = [p]
    r = req("CLOSE_USER_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name)
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.BLOCKED
    assert not any(c[0] in ("post_close", "terminate") for c in ops.calls)


def test_pid_reuse_is_detected(executor, ops):
    p = proc(5000, "discord.exe")
    ops.processes = [p]
    r = req("CLOSE_USER_PROCESS", pid=5000, create_time=p.create_time + 100, name="discord.exe")
    assert executor.execute(r, approve(r)).status == ResultStatus.BLOCKED


def test_close_process_success_and_record(executor, ops, repo):
    p = proc(5000, "discord.exe", private=900 * MB)
    ops.processes = [p]
    r = req("CLOSE_USER_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name)
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.SUCCESS
    assert ("post_close", 5000) in ops.calls and not any(c[0] == "terminate" for c in ops.calls)
    rec = repo.get_action(out.record_id)
    assert rec["result"] == "SUCCESS" and rec["rollback_available"] == 0


def test_unresponsive_app_not_forced_without_consent(executor, ops):
    ops.close_works = False
    p = proc(5000, "stuck.exe")
    ops.processes = [p]
    r = req("CLOSE_USER_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name)
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.FAILED and "Nothing was forced" in out.message
    assert not any(c[0] == "terminate" for c in ops.calls)

    r2 = req("CLOSE_USER_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name, force_if_unresponsive=True)
    out2 = executor.execute(r2, approve(r2))
    assert out2.status == ResultStatus.SUCCESS
    assert ("terminate", 5000) in ops.calls


def test_restart_relaunches_only_the_verified_executable(executor, ops):
    p = proc(5000, "app.exe", private=2000 * MB)
    ops.processes = [p]
    ops.cmdlines[5000] = ["C:\\Evil\\other.exe", "--payload"]  # mismatching argv[0] must not be used
    r = req("RESTART_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name)
    executor.execute(r, approve(r))
    launches = [c for c in ops.calls if c[0] == "launch"]
    assert launches == [("launch", (p.exe,))]


def test_restart_verification_is_honest_when_no_improvement(executor, ops):
    p = proc(5000, "app.exe", private=500 * MB)
    ops.processes = [p]

    def relaunch(argv, cwd):
        ops.calls.append(("launch", tuple(argv)))
        ops.processes.append(proc(6000, "app.exe", private=495 * MB, create_time=p.create_time + 10**9))
        return True

    ops.launch = relaunch
    r = req("RESTART_PROCESS", pid=p.pid, create_time=p.create_time, name=p.name)
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.NO_MEANINGFUL_CHANGE
    assert "No meaningful improvement" in out.message


def test_trim_working_set_reports_private_unchanged(executor, ops):
    p = proc(5000, "app.exe", private=800 * MB, rss=800 * MB)
    ops.processes = [p]
    r = req("TRIM_PROCESS_WORKING_SET", pid=p.pid, create_time=p.create_time, name=p.name)
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.SUCCESS and "unchanged" in out.message


def test_power_plan_must_exist(executor, ops):
    ops.plans = [PowerPlan("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced", True)]
    r = req("CHANGE_POWER_PLAN", guid="8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    assert executor.execute(r, approve(r)).status == ResultStatus.BLOCKED


def test_admin_action_without_broker_fails_cleanly(executor, ops):
    r = req("CREATE_RESTORE_POINT")
    out = executor.execute(r, approve(r))
    assert out.status == ResultStatus.FAILED and "Administrator" in out.message
    assert ops.calls == []


def test_expired_approval_rejected(executor):
    r = req("FLUSH_DNS")
    a = approve(r)
    a.approved_at -= 3600
    assert executor.execute(r, a).status == ResultStatus.REJECTED


def test_no_shell_or_eval_in_action_code():
    """Static guard: the action layer never shells out or evaluates text."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "boostai"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("os.system(", "shell=True", "eval(", "exec(", "powershell", "cmd.exe /c"):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


def test_mode_enum_values():
    assert OptimizationMode("safe") == OptimizationMode.SAFE
