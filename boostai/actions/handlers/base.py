"""Handler contract shared by every whitelisted action."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from boostai.actions.action_models import (
    ActionID,
    ExecutionResult,
    Reversibility,
    RiskLevel,
    Verification,
)
from boostai.actions.system_ops import SystemOps
from boostai.config.settings import Settings


@dataclass(slots=True)
class ActionContext:
    ops: SystemOps
    settings: Settings
    self_pid: int
    user: str
    is_admin: bool
    sleep: Callable[[float], None] = time.sleep
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_id: ActionID
    name: str
    description: str
    risk: RiskLevel
    reversibility: Reversibility
    affected_component: str
    requirements: tuple[str, ...]
    validation_checks: tuple[str, ...]
    execution_method: str
    verification_method: str
    rollback_method: str | None = None
    warning: str | None = None


class NotReversibleError(RuntimeError):
    pass


class ActionHandler(ABC):
    definition: ActionDefinition
    params_model: type[BaseModel]

    def requires_admin(self, params: BaseModel, ctx: ActionContext) -> bool:
        return False

    def admin_reason(self, params: BaseModel) -> str:
        return "it changes a machine-wide setting"

    @abstractmethod
    def check(self, params: BaseModel, ctx: ActionContext) -> tuple[list[str], list[str]]:
        """Return (blocking reasons, warnings). Any blocking reason stops the action."""

    @abstractmethod
    def describe_target(self, params: BaseModel) -> str: ...

    @abstractmethod
    def capture_state(self, params: BaseModel, ctx: ActionContext) -> dict[str, Any]: ...

    @abstractmethod
    def execute(self, params: BaseModel, ctx: ActionContext, state: dict[str, Any]) -> ExecutionResult: ...

    @abstractmethod
    def verify(
        self, params: BaseModel, ctx: ActionContext, state: dict[str, Any], result: ExecutionResult
    ) -> Verification: ...

    @property
    def reversible(self) -> bool:
        return self.definition.reversibility in (Reversibility.REVERSIBLE, Reversibility.PARTIALLY_REVERSIBLE)

    def rollback(
        self, params: BaseModel, ctx: ActionContext, previous_state: dict[str, Any], new_state: dict[str, Any]
    ) -> ExecutionResult:
        raise NotReversibleError(f"{self.definition.action_id.value} cannot be rolled back")
