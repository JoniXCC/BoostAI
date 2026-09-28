"""Whitelisted action executor.

There is deliberately no code path in BoostAI that turns text into a command.
``execute`` only ever dispatches to a registered handler, after validation and approval.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any, Callable

from boostai.actions.action_models import (
    ActionOutcome,
    ActionRequest,
    Approval,
    ExecutionResult,
    ResultStatus,
    ValidationResult,
    Verification,
)
from boostai.actions.elevation import ElevationBroker
from boostai.actions.handlers.base import ActionContext
from boostai.actions.registry import ActionRegistry
from boostai.actions.validator import SafetyValidator
from boostai.config.logging_config import log_event
from boostai.config.settings import OptimizationMode
from boostai.database.repository import Repository

log = logging.getLogger(__name__)

APPROVAL_MAX_AGE_S = 30 * 60


def verification_to_dict(v: Verification | None) -> dict[str, Any] | None:
    if v is None:
        return None
    d = asdict(v)
    d["status"] = v.status.value
    return d


def verification_from_dict(d: dict[str, Any] | None) -> Verification | None:
    if not d:
        return None
    return Verification(ResultStatus(d["status"]), d.get("summary", ""), d.get("before", {}), d.get("after", {}),
                        d.get("improvement", {}))


def outcome_to_dict(o: ActionOutcome) -> dict[str, Any]:
    return {
        "request_id": o.request_id, "action_id": o.action_id, "target": o.target, "status": o.status.value,
        "message": o.message, "verification": verification_to_dict(o.verification),
        "previous_state": o.previous_state, "new_state": o.new_state, "rollback_available": o.rollback_available,
    }


def outcome_from_dict(d: dict[str, Any], request: ActionRequest) -> ActionOutcome:
    try:
        status = ResultStatus(d.get("status", "FAILED"))
    except ValueError:
        status = ResultStatus.FAILED
    return ActionOutcome(
        request_id=request.request_id, action_id=request.action_id, target=str(d.get("target", "")),
        status=status, message=str(d.get("message", "")), verification=verification_from_dict(d.get("verification")),
        previous_state=d.get("previous_state"), new_state=d.get("new_state"),
        rollback_available=bool(d.get("rollback_available")),
    )


class ActionExecutor:
    def __init__(
        self,
        registry: ActionRegistry,
        context_factory: Callable[[], ActionContext],
        repo: Repository | None = None,
        broker: ElevationBroker | None = None,
        mode_provider: Callable[[], OptimizationMode] | None = None,
    ) -> None:
        self.registry = registry
        self.context_factory = context_factory
        self.repo = repo
        self.broker = broker
        self.validator = SafetyValidator(registry, mode_provider or (lambda: OptimizationMode.BALANCED))

    # ---------------------------------------------------------------- public
    def validate(self, request: ActionRequest) -> ValidationResult:
        return self.validator.validate(request, self.context_factory())

    def execute(self, request: ActionRequest, approval: Approval | None, session_id: str | None = None) -> ActionOutcome:
        target = self._target(request)
        if approval is None or approval.request_id != request.request_id:
            return self._finish(request, target, ResultStatus.REJECTED, "Not approved by the user.", session_id=session_id)
        if time.time() - approval.approved_at > APPROVAL_MAX_AGE_S:
            return self._finish(request, target, ResultStatus.REJECTED, "Approval expired; please review again.",
                                session_id=session_id)

        ctx = self.context_factory()
        validation = self.validator.validate(request, ctx)
        if not validation.ok:
            status = validation.status or ResultStatus.BLOCKED
            log_event("action_blocked" if status == ResultStatus.BLOCKED else "action_rejected",
                      action_id=request.action_id, target=target, reasons=validation.reasons, source=request.source)
            return self._finish(request, target, status, " ".join(validation.reasons), session_id=session_id)
        log_event("action_approved", action_id=request.action_id, target=target, source=request.source)

        if validation.requires_admin:
            if self.broker is None:
                return self._finish(request, target, ResultStatus.FAILED,
                                    "Administrator permission is required for this action.", session_id=session_id)
            result = self.broker.run({"op": "execute", "action_id": request.action_id, "params": request.params,
                                      "source": request.source})
            outcome = outcome_from_dict(result, request)
            outcome.target = outcome.target or target
            return self._record(request, outcome, session_id)

        outcome = self.run_local(request, ctx, validation)
        return self._record(request, outcome, session_id)

    def run_local(self, request: ActionRequest, ctx: ActionContext, validation: ValidationResult) -> ActionOutcome:
        """Capture -> execute -> verify in the current process (also used by the elevated helper)."""
        handler = self.registry.get(request.action_id)
        assert handler is not None and validation.params is not None  # guaranteed by validation
        params = validation.params
        target = handler.describe_target(params)
        try:
            state = handler.capture_state(params, ctx)
            result = handler.execute(params, ctx, state)
        except Exception as exc:
            log.exception("Action %s failed", request.action_id)
            log_event("action_failed", action_id=request.action_id, target=target, error=str(exc))
            return ActionOutcome(request.request_id, request.action_id, target, ResultStatus.FAILED,
                                 f"Action failed: {exc}")
        log_event("action_executed", action_id=request.action_id, target=target, success=result.success,
                  message=result.message)
        try:
            verification = handler.verify(params, ctx, state, result)
        except Exception as exc:
            log.exception("Verification of %s failed", request.action_id)
            verification = Verification(ResultStatus.FAILED if not result.success else ResultStatus.PARTIAL,
                                        f"{result.message} Verification could not complete: {exc}")
        log_event("verification_completed", action_id=request.action_id, target=target,
                  status=verification.status.value, summary=verification.summary)
        changed = result.success or result.partial
        return ActionOutcome(
            request_id=request.request_id, action_id=request.action_id, target=target,
            status=verification.status, message=verification.summary or result.message,
            verification=verification, previous_state=state, new_state=result.new_state,
            rollback_available=handler.reversible and changed and bool(state),
        )

    # --------------------------------------------------------------- helpers
    def _target(self, request: ActionRequest) -> str:
        handler = self.registry.get(request.action_id)
        if handler is None:
            return "unknown"
        try:
            return handler.describe_target(handler.params_model.model_validate(request.params))
        except Exception:
            return "invalid parameters"

    def _finish(self, request: ActionRequest, target: str, status: ResultStatus, message: str,
                session_id: str | None) -> ActionOutcome:
        outcome = ActionOutcome(request.request_id, request.action_id, target, status, message)
        return self._record(request, outcome, session_id)

    def _record(self, request: ActionRequest, outcome: ActionOutcome, session_id: str | None) -> ActionOutcome:
        if outcome.status == ResultStatus.FAILED:
            log_event("action_failed", action_id=outcome.action_id, target=outcome.target, message=outcome.message)
        if self.repo is not None:
            definition = self.registry.definition(request.action_id)
            outcome.record_id = self.repo.insert_action(
                {
                    "action_id": request.action_id if definition else f"UNKNOWN:{str(request.action_id)[:60]}",
                    "target": outcome.target,
                    "params": request.params if definition else {},
                    "previous_state": outcome.previous_state,
                    "new_state": outcome.new_state,
                    "result": outcome.status.value,
                    "verification": verification_to_dict(outcome.verification),
                    "message": outcome.message,
                    "reversibility": definition.reversibility.value if definition else None,
                    "rollback_available": outcome.rollback_available,
                    "session_id": session_id,
                }
            )
        return outcome


def run_rollback_local(registry: ActionRegistry, ctx: ActionContext, action_id: str, params: dict,
                       previous_state: dict, new_state: dict) -> ExecutionResult:
    handler = registry.get(action_id)
    if handler is None:
        return ExecutionResult(False, "Unknown action ID - rollback rejected.")
    if not handler.reversible:
        return ExecutionResult(False, f"{handler.definition.name} is not reversible.")
    parsed = handler.params_model.model_validate(params)
    return handler.rollback(parsed, ctx, previous_state or {}, new_state or {})
