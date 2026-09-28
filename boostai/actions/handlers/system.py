"""Power plan, DNS cache and System Restore actions."""

from __future__ import annotations

from typing import Any

import psutil

from boostai.actions.action_models import (
    ActionID,
    ExecutionResult,
    NoParams,
    PowerPlanParams,
    Reversibility,
    ResultStatus,
    RiskLevel,
    Verification,
)
from boostai.actions.handlers.base import ActionContext, ActionDefinition, ActionHandler

RESTORE_POINT_DESCRIPTION = "BoostAI - before optimization"


class PowerPlanHandler(ActionHandler):
    params_model = PowerPlanParams
    definition = ActionDefinition(
        action_id=ActionID.CHANGE_POWER_PLAN,
        name="Switch power plan",
        description="Activates one of the power plans that already exist on this PC.",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.REVERSIBLE,
        affected_component="Windows power plan",
        requirements=("Target plan must already exist",),
        validation_checks=("GUID format", "GUID must be one of the enumerated plans"),
        execution_method="PowerSetActiveScheme (powrprof.dll)",
        verification_method="Re-reads the active scheme",
        rollback_method="Re-activates the previous plan",
    )

    def describe_target(self, params: PowerPlanParams) -> str:
        return params.guid

    def check(self, params, ctx) -> tuple[list[str], list[str]]:
        plans = ctx.ops.list_power_plans()
        plan = next((p for p in plans if p.guid.lower() == params.guid.lower()), None)
        if plan is None:
            return ["That power plan does not exist on this PC."], []
        if plan.active:
            return [f"'{plan.name}' is already the active plan."], []
        warnings = []
        try:
            battery = psutil.sensors_battery()
        except (RuntimeError, OSError, NotImplementedError):
            battery = None
        if battery is not None and not battery.power_plugged:
            warnings.append("You are on battery: higher-performance plans reduce battery life and increase heat.")
        return [], warnings

    def capture_state(self, params, ctx) -> dict[str, Any]:
        active = next((p for p in ctx.ops.list_power_plans() if p.active), None)
        return {"guid": active.guid if active else None, "name": active.name if active else None}

    def execute(self, params, ctx, state) -> ExecutionResult:
        ok = ctx.ops.set_power_plan(params.guid)
        name = next((p.name for p in ctx.ops.list_power_plans() if p.guid.lower() == params.guid.lower()), params.guid)
        return ExecutionResult(ok, f"Power plan set to '{name}'." if ok else "Windows rejected the power plan change.",
                               new_state={"guid": params.guid, "name": name})

    def verify(self, params, ctx, state, result) -> Verification:
        active = next((p for p in ctx.ops.list_power_plans() if p.active), None)
        before = {"plan": state.get("name")}
        after = {"plan": active.name if active else None}
        if active and active.guid.lower() == params.guid.lower():
            return Verification(ResultStatus.SUCCESS, f"Verified: active plan is now '{active.name}'.", before, after, {})
        return Verification(ResultStatus.FAILED, "The active plan did not change.", before, after, {})

    def rollback(self, params, ctx, previous_state, new_state) -> ExecutionResult:
        guid = previous_state.get("guid")
        if not guid:
            return ExecutionResult(False, "Previous plan unknown.")
        ok = ctx.ops.set_power_plan(guid)
        return ExecutionResult(ok, f"Restored power plan '{previous_state.get('name')}'." if ok else "Could not restore plan.")


class FlushDnsHandler(ActionHandler):
    params_model = NoParams
    definition = ActionDefinition(
        action_id=ActionID.FLUSH_DNS,
        name="Flush DNS cache",
        description="Clears the Windows DNS resolver cache. Fixes stale name resolution; it does not speed up the PC.",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.NOT_APPLICABLE,
        affected_component="DNS client cache",
        requirements=(),
        validation_checks=("No parameters accepted",),
        execution_method="DnsFlushResolverCache (dnsapi.dll)",
        verification_method="API return value",
    )

    def describe_target(self, params) -> str:
        return "DNS resolver cache"

    def check(self, params, ctx):
        return [], []

    def capture_state(self, params, ctx):
        return {}

    def execute(self, params, ctx, state) -> ExecutionResult:
        ok = ctx.ops.flush_dns()
        return ExecutionResult(ok, "DNS cache flushed." if ok else "DNS cache flush failed.")

    def verify(self, params, ctx, state, result) -> Verification:
        if result.success:
            return Verification(ResultStatus.SUCCESS,
                                "DNS cache flushed. This resolves stale DNS entries; no performance change is expected.")
        return Verification(ResultStatus.FAILED, result.message)


class RestorePointHandler(ActionHandler):
    params_model = NoParams
    definition = ActionDefinition(
        action_id=ActionID.CREATE_RESTORE_POINT,
        name="Create System Restore point",
        description="Asks Windows System Restore to create a restore point before changes are made.",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.NOT_APPLICABLE,
        affected_component="System Restore",
        requirements=("Administrator permission", "System Protection enabled on the system drive"),
        validation_checks=("No parameters accepted",),
        execution_method="WMI SystemRestore.CreateRestorePoint",
        verification_method="Checks that the newest restore point is BoostAI's",
    )

    def describe_target(self, params) -> str:
        return "System Restore"

    def requires_admin(self, params, ctx) -> bool:
        return True

    def admin_reason(self, params) -> str:
        return "Windows only lets administrators create restore points"

    def check(self, params, ctx):
        return [], ["Windows creates at most one restore point per 24 hours by default."]

    def capture_state(self, params, ctx):
        latest = ctx.ops.latest_restore_point()
        return {"sequence": latest[0] if latest else 0}

    def execute(self, params, ctx, state) -> ExecutionResult:
        ok = ctx.ops.create_restore_point(RESTORE_POINT_DESCRIPTION)
        return ExecutionResult(ok, "Restore point requested." if ok else "Restore point request failed.")

    def verify(self, params, ctx, state, result) -> Verification:
        latest = ctx.ops.latest_restore_point()
        before = {"sequence": state.get("sequence", 0)}
        after = {"sequence": latest[0] if latest else 0}
        created = latest is not None and latest[0] > int(state.get("sequence", 0)) and latest[1] == RESTORE_POINT_DESCRIPTION
        if result.success and created:
            return Verification(ResultStatus.SUCCESS, "Verified: a new restore point was created.", before, after)
        if result.success:
            return Verification(
                ResultStatus.NO_MEANINGFUL_CHANGE,
                "Windows did not create a new restore point (System Protection may be off, or one was created in the "
                "last 24 hours).", before, after,
            )
        return Verification(ResultStatus.FAILED, result.message, before, after)
