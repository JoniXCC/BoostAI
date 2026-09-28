"""Entry point of the short-lived elevated helper (``--elevated-helper <request file>``).

The helper performs exactly one whitelisted operation and exits. It refuses:
* request files outside BoostAI's elevation folder;
* unknown action IDs or malformed parameters (same validator as the UI);
* actions that do not actually require administrator rights (they must run unelevated).
"""

from __future__ import annotations

import json
import logging
import os

from boostai.actions.action_models import ActionRequest, ResultStatus
from boostai.actions.elevation import validate_request_path, write_result
from boostai.actions.executor import ActionExecutor, outcome_to_dict, run_rollback_local
from boostai.actions.handlers.base import ActionContext
from boostai.actions.registry import ActionRegistry
from boostai.actions.system_ops import WindowsSystemOps
from boostai.config.logging_config import configure_logging
from boostai.config.settings import SettingsStore
from boostai.security.protected_processes import current_username
from boostai.utils import winapi

log = logging.getLogger(__name__)


def run_helper(path_str: str) -> int:
    configure_logging()
    try:
        request_path = validate_request_path(path_str)
    except (PermissionError, FileNotFoundError) as exc:
        log.error("Elevated helper refused request: %s", exc)
        return 2
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        write_result(request_path, {"status": "FAILED", "message": f"Unreadable request: {exc}"})
        return 3
    try:
        result = handle_payload(payload)
    except Exception as exc:  # never leave the caller without an answer
        log.exception("Elevated helper failure")
        result = {"status": "FAILED", "message": f"Elevated helper error: {exc}"}
    write_result(request_path, result)
    return 0


def handle_payload(payload: dict, ops=None, settings=None) -> dict:
    registry = ActionRegistry()
    settings = settings or SettingsStore().settings
    ctx = ActionContext(ops=ops or WindowsSystemOps(), settings=settings, self_pid=os.getpid(),
                        user=current_username(), is_admin=winapi.is_admin() if ops is None else True)
    action_id = payload.get("action_id")
    handler = registry.get(action_id)
    if handler is None:
        return {"status": ResultStatus.REJECTED.value, "message": f"Unknown action ID '{action_id}' - rejected."}
    try:
        params = handler.params_model.model_validate(payload.get("params") or {})
    except Exception as exc:
        return {"status": ResultStatus.REJECTED.value, "message": f"Invalid parameters: {exc}"}
    if not handler.requires_admin(params, ctx):
        return {"status": ResultStatus.REJECTED.value,
                "message": "This action does not need administrator rights and is refused by the elevated helper."}

    op = payload.get("op")
    if op == "execute":
        request = ActionRequest(action_id=action_id, params=payload.get("params") or {},
                                source=str(payload.get("source", "user")))
        executor = ActionExecutor(registry, lambda: ctx, mode_provider=lambda: settings.mode)
        validation = executor.validator.validate(request, ctx)
        if not validation.ok:
            status = validation.status or ResultStatus.BLOCKED
            return {"status": status.value, "message": " ".join(validation.reasons),
                    "target": handler.describe_target(params)}
        return outcome_to_dict(executor.run_local(request, ctx, validation))
    if op == "rollback":
        result = run_rollback_local(registry, ctx, action_id, payload.get("params") or {},
                                    payload.get("previous_state") or {}, payload.get("new_state") or {})
        return {"status": "SUCCESS" if result.success else "FAILED", "message": result.message}
    return {"status": ResultStatus.REJECTED.value, "message": f"Unknown operation '{op}'."}
