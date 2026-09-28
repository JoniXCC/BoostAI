"""Enable/disable startup items via the Task-Manager-compatible StartupApproved flag."""

from __future__ import annotations

from typing import Any

from boostai.actions.action_models import (
    ActionID,
    ExecutionResult,
    Reversibility,
    ResultStatus,
    RiskLevel,
    StartupParams,
    Verification,
)
from boostai.actions.handlers.base import ActionContext, ActionDefinition, ActionHandler
from boostai.core.models import StartupItem, StartupSource
from boostai.knowledge.startup_catalog import StartupCategory, assess
from boostai.metrics.startup import ENABLED_BYTES, approved_value_name, disabled_bytes, is_enabled_state


def _find(ctx: ActionContext, item_id: str) -> StartupItem | None:
    return next((i for i in ctx.ops.list_startup_items() if i.item_id == item_id), None)


class _StartupToggle(ActionHandler):
    params_model = StartupParams
    enable: bool = False

    def describe_target(self, params: StartupParams) -> str:
        return params.item_id.split(":", 1)[-1]

    def requires_admin(self, params: StartupParams, ctx: ActionContext) -> bool:
        source = params.item_id.split(":", 1)[0]
        try:
            return StartupSource(source).requires_admin
        except ValueError:
            return False

    def admin_reason(self, params) -> str:
        return "this startup entry is registered for all users (HKEY_LOCAL_MACHINE)"

    def check(self, params: StartupParams, ctx: ActionContext) -> tuple[list[str], list[str]]:
        item = _find(ctx, params.item_id)
        if item is None:
            return ["Startup item not found (it may have been removed)."], []
        if item.enabled == self.enable:
            return [f"Startup item is already {'enabled' if self.enable else 'disabled'}."], []
        warnings: list[str] = []
        if not self.enable:
            assessment = assess(item, [])
            if assessment.category == StartupCategory.PROTECTED:
                return ["Protected startup item (security software or Windows component)."], []
            if assessment.category == StartupCategory.RECOMMENDED_KEEP:
                warnings.append("This looks like a hardware/driver utility; disabling it may break device features.")
            if assessment.category == StartupCategory.UNKNOWN:
                warnings.append("BoostAI does not recognise this item. Make sure you know what it is.")
        return [], warnings

    def capture_state(self, params: StartupParams, ctx: ActionContext) -> dict[str, Any]:
        item = _find(ctx, params.item_id)
        if item is None:
            return {}
        name = approved_value_name(item)
        raw = ctx.ops.read_startup_state(item.source, name)
        return {
            "item_id": item.item_id,
            "name": item.name,
            "source": item.source.value,
            "value_name": name,
            "raw": raw.hex() if raw else None,
            "enabled": is_enabled_state(raw),
        }

    def execute(self, params: StartupParams, ctx: ActionContext, state: dict[str, Any]) -> ExecutionResult:
        if not state:
            return ExecutionResult(False, "Startup item not found.")
        data = ENABLED_BYTES if self.enable else disabled_bytes()
        try:
            ctx.ops.write_startup_state(StartupSource(state["source"]), state["value_name"], data)
        except PermissionError:
            return ExecutionResult(False, "Windows denied the change (administrator permission required).")
        except OSError as exc:
            return ExecutionResult(False, f"Registry write failed: {exc.strerror or exc}")
        return ExecutionResult(True, f"{state['name']} {'enabled' if self.enable else 'disabled'} at startup.",
                               new_state={"enabled": self.enable, "raw": data.hex()})

    def verify(self, params, ctx, state, result) -> Verification:
        before = {"enabled": state.get("enabled")}
        if not result.success:
            return Verification(ResultStatus.FAILED, result.message, before, {}, {})
        raw = ctx.ops.read_startup_state(StartupSource(state["source"]), state["value_name"])
        now_enabled = is_enabled_state(raw)
        after = {"enabled": now_enabled}
        if now_enabled == self.enable:
            return Verification(
                ResultStatus.SUCCESS,
                f"Verified: {state['name']} is now {'enabled' if self.enable else 'disabled'} at startup. "
                "The effect on boot/sign-in time applies from the next sign-in.",
                before, after, {"startup_enabled_change": 1 if self.enable else -1},
            )
        return Verification(ResultStatus.FAILED, "The setting did not change as expected.", before, after, {})

    def rollback(self, params, ctx, previous_state, new_state) -> ExecutionResult:
        raw = previous_state.get("raw")
        data = bytes.fromhex(raw) if raw else None
        try:
            ctx.ops.write_startup_state(StartupSource(previous_state["source"]), previous_state["value_name"], data)
        except OSError as exc:
            return ExecutionResult(False, f"Could not restore the previous startup state: {exc}")
        restored = is_enabled_state(ctx.ops.read_startup_state(StartupSource(previous_state["source"]), previous_state["value_name"]))
        if restored != previous_state.get("enabled", True):
            return ExecutionResult(False, "Rollback write succeeded but the state did not match the original.")
        return ExecutionResult(True, f"Restored {previous_state['name']} to {'enabled' if restored else 'disabled'}.")


class DisableStartupHandler(_StartupToggle):
    enable = False
    definition = ActionDefinition(
        action_id=ActionID.DISABLE_STARTUP_ITEM,
        name="Disable at startup",
        description="Stops a program from starting automatically at sign-in (same mechanism as Task Manager).",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.REVERSIBLE,
        affected_component="Startup programs",
        requirements=("Item exists", "Not a protected item", "Admin only for all-users entries"),
        validation_checks=("Item ID must match a discovered entry", "Protected-item rules"),
        execution_method="Writes the StartupApproved flag; the program's own entry is never deleted",
        verification_method="Re-reads the StartupApproved flag",
        rollback_method="Restores the exact previous StartupApproved value",
    )


class EnableStartupHandler(_StartupToggle):
    enable = True
    definition = ActionDefinition(
        action_id=ActionID.ENABLE_STARTUP_ITEM,
        name="Enable at startup",
        description="Re-enables a previously disabled startup program.",
        risk=RiskLevel.LOW,
        reversibility=Reversibility.REVERSIBLE,
        affected_component="Startup programs",
        requirements=("Item exists", "Admin only for all-users entries"),
        validation_checks=("Item ID must match a discovered entry",),
        execution_method="Writes the StartupApproved 'enabled' flag",
        verification_method="Re-reads the StartupApproved flag",
        rollback_method="Restores the exact previous StartupApproved value",
    )
