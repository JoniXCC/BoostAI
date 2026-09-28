"""Undo support for reversible actions, driven entirely by stored audit records."""

from __future__ import annotations

import logging
from typing import Callable

from boostai.actions.action_models import ExecutionResult
from boostai.actions.elevation import ElevationBroker
from boostai.actions.executor import run_rollback_local
from boostai.actions.handlers.base import ActionContext
from boostai.actions.registry import ActionRegistry
from boostai.config.logging_config import log_event
from boostai.database.repository import Repository

log = logging.getLogger(__name__)


class RollbackManager:
    def __init__(self, registry: ActionRegistry, context_factory: Callable[[], ActionContext], repo: Repository,
                 broker: ElevationBroker | None = None) -> None:
        self.registry = registry
        self.context_factory = context_factory
        self.repo = repo
        self.broker = broker

    def can_rollback(self, record: dict) -> tuple[bool, str]:
        handler = self.registry.get(record.get("action_id"))
        if handler is None:
            return False, "Unknown action."
        if not handler.reversible:
            return False, f"{handler.definition.name} is {handler.definition.reversibility.label.lower()}."
        if record.get("rolled_back"):
            return False, "Already undone."
        if not record.get("rollback_available"):
            return False, "No rollback data was stored (the action did not change anything)."
        return True, "Reversible"

    def rollback(self, record_id: int) -> ExecutionResult:
        record = self.repo.get_action(record_id)
        if record is None:
            return ExecutionResult(False, "Action record not found.")
        ok, reason = self.can_rollback(record)
        if not ok:
            return ExecutionResult(False, reason)
        action_id, params = record["action_id"], record.get("params") or {}
        log_event("rollback_started", record_id=record_id, action_id=action_id, target=record.get("target"))
        handler = self.registry.get(action_id)
        ctx = self.context_factory()
        try:
            parsed = handler.params_model.model_validate(params)
            needs_admin = handler.requires_admin(parsed, ctx) and not ctx.is_admin
        except Exception as exc:
            return ExecutionResult(False, f"Stored parameters are invalid: {exc}")

        if needs_admin:
            if self.broker is None:
                result = ExecutionResult(False, "Administrator permission is required to undo this change.")
            else:
                data = self.broker.run({
                    "op": "rollback", "action_id": action_id, "params": params,
                    "previous_state": record.get("previous_state") or {}, "new_state": record.get("new_state") or {},
                })
                result = ExecutionResult(data.get("status") == "SUCCESS", str(data.get("message", "")))
        else:
            try:
                result = run_rollback_local(self.registry, ctx, action_id, params,
                                            record.get("previous_state") or {}, record.get("new_state") or {})
            except Exception as exc:
                log.exception("Rollback failed")
                result = ExecutionResult(False, f"Rollback failed: {exc}")

        self.repo.mark_rolled_back(record_id, "SUCCESS" if result.success else "FAILED")
        log_event("rollback_completed" if result.success else "rollback_failed", record_id=record_id,
                  action_id=action_id, message=result.message)
        return result

    def rollback_session(self, session_id: str) -> list[tuple[int, ExecutionResult]]:
        """Undo every reversible change of a session (e.g. Gaming Mode), newest first."""
        results = []
        for record in reversed(self.repo.actions_for_session(session_id)):
            if self.can_rollback(record)[0]:
                results.append((record["id"], self.rollback(record["id"])))
        return results
