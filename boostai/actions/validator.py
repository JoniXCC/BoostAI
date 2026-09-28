"""Safety validator: the gate every request passes before it can be approved or executed."""

from __future__ import annotations

from typing import Callable

from pydantic import ValidationError

from boostai.actions.action_models import ActionRequest, ResultStatus, ValidationResult
from boostai.actions.handlers.base import ActionContext
from boostai.actions.registry import ActionRegistry
from boostai.config.settings import OptimizationMode
from boostai.security.safety_rules import risk_allowed


class SafetyValidator:
    def __init__(self, registry: ActionRegistry, mode_provider: Callable[[], OptimizationMode]) -> None:
        self.registry = registry
        self.mode_provider = mode_provider

    def validate(self, request: ActionRequest, ctx: ActionContext, *, enforce_mode: bool = True) -> ValidationResult:
        # 1. Whitelist: unknown action IDs are rejected outright.
        handler = self.registry.get(request.action_id)
        if handler is None:
            return ValidationResult(False, [f"Unknown action ID '{request.action_id}' - rejected."],
                                    status=ResultStatus.REJECTED)
        # 2. Strict parameter schema (extra fields forbidden, types/ranges enforced).
        try:
            params = handler.params_model.model_validate(request.params)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'params'}: {e['msg']}" for e in exc.errors())
            return ValidationResult(False, [f"Invalid parameters - rejected ({problems})."], status=ResultStatus.REJECTED)
        # 3. Optimisation-mode risk policy.
        definition = handler.definition
        if enforce_mode and not risk_allowed(definition.risk.value, self.mode_provider()):
            return ValidationResult(
                False,
                [f"{definition.name} is {definition.risk.value} risk and is not allowed in "
                 f"{self.mode_provider().value.upper()} mode (shown as a recommendation only)."],
                params=params, status=ResultStatus.BLOCKED,
            )
        # 4. Deterministic, action-specific safety rules against the *current* system state.
        try:
            reasons, warnings = handler.check(params, ctx)
        except Exception as exc:  # a check that cannot complete means "not safe"
            return ValidationResult(False, [f"Safety check could not complete: {exc}"], params=params,
                                    status=ResultStatus.BLOCKED)
        if definition.warning and definition.warning not in warnings:
            warnings = [definition.warning, *warnings]
        if reasons:
            return ValidationResult(False, reasons, warnings, params=params, status=ResultStatus.BLOCKED)
        return ValidationResult(
            True, [], warnings, requires_admin=handler.requires_admin(params, ctx) and not ctx.is_admin, params=params
        )
