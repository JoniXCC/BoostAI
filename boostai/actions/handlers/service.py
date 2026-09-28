"""Service actions - only for services on the explicit OPTIONAL allow-list.

Start types are never changed: a stopped optional service returns on the next reboot
(or when Windows needs it), which keeps these actions low-consequence.
"""

from __future__ import annotations

from typing import Any

from boostai.actions.action_models import (
    ActionID,
    ExecutionResult,
    Reversibility,
    ResultStatus,
    RiskLevel,
    ServiceParams,
    Verification,
)
from boostai.actions.handlers.base import ActionContext, ActionDefinition, ActionHandler
from boostai.core.models import fmt_bytes
from boostai.security.protected_services import ServiceClass, classify_service, optional_service


class _ServiceBase(ActionHandler):
    params_model = ServiceParams
    needs_status: str | None = None

    def describe_target(self, params: ServiceParams) -> str:
        return params.name

    def requires_admin(self, params, ctx) -> bool:
        return True

    def admin_reason(self, params) -> str:
        return "starting or stopping Windows services requires administrator rights"

    def check(self, params: ServiceParams, ctx: ActionContext) -> tuple[list[str], list[str]]:
        cls, why = classify_service(params.name)
        if cls != ServiceClass.OPTIONAL:
            return [f"Service '{params.name}' is not on BoostAI's optional-service allow-list ({why})."], []
        svc = ctx.ops.get_service(params.name)
        if svc is None:
            return [f"Service '{params.name}' was not found."], []
        if self.needs_status and svc.status != self.needs_status:
            return [f"Service is {svc.status}, expected {self.needs_status}."], []
        return self.extra_checks(params), [optional_service(params.name).description]

    def extra_checks(self, params: ServiceParams) -> list[str]:
        return []

    def capture_state(self, params: ServiceParams, ctx: ActionContext) -> dict[str, Any]:
        svc = ctx.ops.get_service(params.name)
        mem = 0
        if svc and svc.pid:
            mem = next((p.private for p in ctx.ops.list_processes() if p.pid == svc.pid), 0)
        return {
            "name": params.name,
            "status": svc.status if svc else "unknown",
            "start_type": svc.start_type if svc else "unknown",
            "pid": svc.pid if svc else None,
            "host_private_bytes": mem,
        }

    def _status(self, ctx, name) -> str:
        svc = ctx.ops.get_service(name)
        return svc.status if svc else "unknown"


class StopServiceHandler(_ServiceBase):
    needs_status = "running"
    definition = ActionDefinition(
        action_id=ActionID.STOP_OPTIONAL_SERVICE,
        name="Stop optional service",
        description="Stops an allow-listed optional service until the next restart. Its start type is not changed.",
        risk=RiskLevel.MEDIUM,
        reversibility=Reversibility.REVERSIBLE,
        affected_component="Windows service",
        requirements=("Service on the optional allow-list", "Administrator permission"),
        validation_checks=("Allow-list membership", "Protected-service rules", "Service currently running"),
        execution_method="Service Control Manager stop request",
        verification_method="Confirms the service reached the Stopped state",
        rollback_method="Starts the service again",
    )

    def extra_checks(self, params) -> list[str]:
        opt = optional_service(params.name)
        return [] if opt and opt.stoppable else [f"'{params.name}' may be restarted but not stopped."]

    def execute(self, params, ctx, state) -> ExecutionResult:
        ok = ctx.ops.stop_service(params.name)
        return ExecutionResult(ok, f"{params.name} stopped." if ok else f"{params.name} did not stop (it may have dependent services).",
                               new_state={"status": "stopped" if ok else self._status(ctx, params.name)})

    def verify(self, params, ctx, state, result) -> Verification:
        status = self._status(ctx, params.name)
        before, after = {"status": state.get("status")}, {"status": status}
        if status == "stopped":
            host = int(state.get("host_private_bytes") or 0)
            note = f" Its host process was using {fmt_bytes(host)} (shared hosts may keep running)." if host else ""
            return Verification(ResultStatus.SUCCESS, f"Verified: {params.name} is stopped.{note}", before, after,
                                {"host_private_bytes": host})
        return Verification(ResultStatus.FAILED, result.message, before, after, {})

    def rollback(self, params, ctx, previous_state, new_state) -> ExecutionResult:
        if previous_state.get("status") != "running":
            return ExecutionResult(True, "Service was not running before; nothing to restore.")
        ok = ctx.ops.start_service(previous_state["name"])
        return ExecutionResult(ok, f"{previous_state['name']} started again." if ok else "Service failed to start.")


class StartServiceHandler(_ServiceBase):
    needs_status = "stopped"
    definition = ActionDefinition(
        action_id=ActionID.RESTORE_OPTIONAL_SERVICE,
        name="Start optional service",
        description="Starts an allow-listed optional service again.",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.REVERSIBLE,
        affected_component="Windows service",
        requirements=("Service on the optional allow-list", "Administrator permission"),
        validation_checks=("Allow-list membership", "Service currently stopped"),
        execution_method="Service Control Manager start request",
        verification_method="Confirms the service reached the Running state",
        rollback_method="Stops the service again",
    )

    def execute(self, params, ctx, state) -> ExecutionResult:
        ok = ctx.ops.start_service(params.name)
        return ExecutionResult(ok, f"{params.name} started." if ok else f"{params.name} did not start.")

    def verify(self, params, ctx, state, result) -> Verification:
        status = self._status(ctx, params.name)
        ok = status == "running"
        return Verification(ResultStatus.SUCCESS if ok else ResultStatus.FAILED,
                            f"Verified: {params.name} is running." if ok else result.message,
                            {"status": state.get("status")}, {"status": status}, {})

    def rollback(self, params, ctx, previous_state, new_state) -> ExecutionResult:
        ok = ctx.ops.stop_service(previous_state["name"])
        return ExecutionResult(ok, "Service stopped again." if ok else "Service could not be stopped.")


class RestartServiceHandler(_ServiceBase):
    needs_status = "running"
    definition = ActionDefinition(
        action_id=ActionID.RESTART_SERVICE,
        name="Restart optional service",
        description="Stops and starts an allow-listed optional service (e.g. a runaway search indexer).",
        risk=RiskLevel.MEDIUM,
        reversibility=Reversibility.NOT_APPLICABLE,
        affected_component="Windows service",
        requirements=("Service on the optional allow-list and restartable", "Administrator permission"),
        validation_checks=("Allow-list membership", "Service currently running"),
        execution_method="Service Control Manager stop + start",
        verification_method="Confirms Running state and compares host-process memory",
    )

    def extra_checks(self, params) -> list[str]:
        opt = optional_service(params.name)
        return [] if opt and opt.restartable else [f"'{params.name}' is not marked restartable."]

    def execute(self, params, ctx, state) -> ExecutionResult:
        if not ctx.ops.stop_service(params.name):
            return ExecutionResult(False, f"{params.name} did not stop; nothing else was changed.")
        ok = ctx.ops.start_service(params.name)
        return ExecutionResult(ok, f"{params.name} restarted." if ok else f"{params.name} stopped but did not start again.",
                               partial=not ok)

    def verify(self, params, ctx, state, result) -> Verification:
        svc = ctx.ops.get_service(params.name)
        status = svc.status if svc else "unknown"
        before = {"status": state.get("status"), "host_private_bytes": state.get("host_private_bytes")}
        if status != "running":
            return Verification(ResultStatus.FAILED, result.message + " Start it from Services if needed.", before,
                                {"status": status}, {})
        after_mem = next((p.private for p in ctx.ops.list_processes() if svc and p.pid == svc.pid), 0)
        freed = int(state.get("host_private_bytes") or 0) - after_mem
        after = {"status": status, "host_private_bytes": after_mem}
        if freed > 50 * 1024 * 1024:
            return Verification(ResultStatus.SUCCESS, f"Restarted; host memory reduced by {fmt_bytes(freed)}.", before, after,
                                {"memory_released": freed})
        return Verification(ResultStatus.NO_MEANINGFUL_CHANGE, "Service restarted; no meaningful memory change measured.",
                            before, after, {"memory_released": freed})
