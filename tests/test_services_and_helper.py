import pytest

from boostai.actions.action_models import ActionRequest, Approval, ResultStatus
from boostai.actions.elevated_helper import handle_payload
from boostai.actions.elevation import validate_request_path
from boostai.config import paths
from boostai.core.models import ServiceInfo
from boostai.security.protected_services import ServiceClass, classify_service


@pytest.mark.parametrize("name", ["WinDefend", "mpssvc", "wuauserv", "RpcSs", "Dnscache", "BFE", "CDPUserSvc_3fa2b"])
def test_critical_services_protected(name):
    assert classify_service(name)[0] == ServiceClass.PROTECTED


def test_optional_and_unknown_services():
    assert classify_service("WSearch")[0] == ServiceClass.OPTIONAL
    cls, why = classify_service("SomeVendorHelperSvc")
    assert cls == ServiceClass.UNKNOWN and why == "Manual review required."
    assert classify_service("VendorUpdateService")[0] == ServiceClass.PROTECTED  # name heuristic: update


def test_stopping_protected_service_blocked(executor, ops):
    ops.services["windefend"] = ServiceInfo("WinDefend", "Defender", "running", "automatic", 1)
    r = ActionRequest(action_id="STOP_OPTIONAL_SERVICE", params={"name": "WinDefend"})
    assert executor.validate(r).status == ResultStatus.BLOCKED


def test_stop_and_rollback_optional_service_via_helper(ops, settings):
    ops.services["wsearch"] = ServiceInfo("WSearch", "Windows Search", "running", "automatic", 55)
    out = handle_payload({"op": "execute", "action_id": "STOP_OPTIONAL_SERVICE", "params": {"name": "WSearch"}},
                         ops=ops, settings=settings)
    assert out["status"] == "SUCCESS" and ops.services["wsearch"].status == "stopped"
    back = handle_payload({"op": "rollback", "action_id": "STOP_OPTIONAL_SERVICE", "params": {"name": "WSearch"},
                           "previous_state": out["previous_state"], "new_state": out["new_state"]},
                          ops=ops, settings=settings)
    assert back["status"] == "SUCCESS" and ops.services["wsearch"].status == "running"


def test_helper_rejects_unknown_and_non_admin_actions(ops, settings):
    assert handle_payload({"op": "execute", "action_id": "DELETE_EVERYTHING"}, ops=ops, settings=settings)["status"] == "REJECTED"
    out = handle_payload({"op": "execute", "action_id": "FLUSH_DNS", "params": {}}, ops=ops, settings=settings)
    assert out["status"] == "REJECTED" and "does not need administrator" in out["message"]
    assert ops.calls == []


def test_helper_rejects_bad_params_and_ops(ops, settings):
    assert handle_payload({"op": "execute", "action_id": "STOP_OPTIONAL_SERVICE", "params": {"name": "a b; c"}},
                          ops=ops, settings=settings)["status"] == "REJECTED"
    assert handle_payload({"op": "shell", "action_id": "CREATE_RESTORE_POINT", "params": {}},
                          ops=ops, settings=settings)["status"] == "REJECTED"


def test_restore_point_verification_via_helper(ops, settings):
    ops.restore_points = [(7, "BoostAI - before optimization")]  # an older BoostAI point must not count
    out = handle_payload({"op": "execute", "action_id": "CREATE_RESTORE_POINT", "params": {}}, ops=ops,
                         settings=settings)
    assert out["status"] == "SUCCESS" and ops.restore_points[-1][0] == 8


def test_request_path_must_be_inside_elevation_dir(tmp_path):
    outside = tmp_path / ("a" * 32 + ".req.json")
    outside.write_text("{}")
    with pytest.raises(PermissionError):
        validate_request_path(str(outside))
    bad_name = paths.elevation_dir() / "evil.json"
    bad_name.write_text("{}")
    with pytest.raises(PermissionError):
        validate_request_path(str(bad_name))
    good = paths.elevation_dir() / ("b" * 32 + ".req.json")
    good.write_text("{}")
    assert validate_request_path(str(good)) == good.resolve()


def test_service_action_requires_admin_in_ui_process(executor, ops):
    ops.services["wsearch"] = ServiceInfo("WSearch", "Windows Search", "running", "automatic", 55)
    r = ActionRequest(action_id="STOP_OPTIONAL_SERVICE", params={"name": "WSearch"})
    v = executor.validate(r)
    assert v.ok and v.requires_admin
    out = executor.execute(r, Approval(r.request_id))  # no broker in tests
    assert out.status == ResultStatus.FAILED and ops.services["wsearch"].status == "running"
