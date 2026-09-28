"""Models for the whitelisted action system.

The flow is fixed and enforced by :class:`boostai.actions.executor.ActionExecutor`::

    proposal (rules or AI) -> ActionRequest(action_id, params)
        -> registry lookup (unknown id => REJECTED)
        -> parameter schema validation (pydantic, extra fields forbidden)
        -> safety validator (deterministic rules)
        -> permission check (elevation only for that action)
        -> explicit user approval
        -> whitelisted handler -> post-action verification -> audit record (+ rollback data)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionID(str, Enum):
    RESTART_PROCESS = "RESTART_PROCESS"
    CLOSE_USER_PROCESS = "CLOSE_USER_PROCESS"
    TRIM_PROCESS_WORKING_SET = "TRIM_PROCESS_WORKING_SET"
    DISABLE_STARTUP_ITEM = "DISABLE_STARTUP_ITEM"
    ENABLE_STARTUP_ITEM = "ENABLE_STARTUP_ITEM"
    CLEAR_SAFE_TEMP_FILES = "CLEAR_SAFE_TEMP_FILES"
    CLEAR_SPECIFIC_SAFE_CACHE = "CLEAR_SPECIFIC_SAFE_CACHE"
    CHANGE_POWER_PLAN = "CHANGE_POWER_PLAN"
    FLUSH_DNS = "FLUSH_DNS"
    STOP_OPTIONAL_SERVICE = "STOP_OPTIONAL_SERVICE"
    RESTORE_OPTIONAL_SERVICE = "RESTORE_OPTIONAL_SERVICE"
    RESTART_SERVICE = "RESTART_SERVICE"
    CREATE_RESTORE_POINT = "CREATE_RESTORE_POINT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Reversibility(str, Enum):
    REVERSIBLE = "REVERSIBLE"
    PARTIALLY_REVERSIBLE = "PARTIALLY_REVERSIBLE"
    NOT_REVERSIBLE = "NOT_REVERSIBLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def label(self) -> str:
        return {
            "REVERSIBLE": "Reversible",
            "PARTIALLY_REVERSIBLE": "Partially reversible",
            "NOT_REVERSIBLE": "Not reversible",
            "NOT_APPLICABLE": "Not applicable",
        }[self.value]


class ResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_MEANINGFUL_CHANGE = "NO_MEANINGFUL_CHANGE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

    @property
    def label(self) -> str:
        return {
            "SUCCESS": "Successful",
            "NO_MEANINGFUL_CHANGE": "No meaningful improvement detected",
            "PARTIAL": "Partially successful",
            "FAILED": "Failed",
            "BLOCKED": "Blocked for safety",
            "REJECTED": "Rejected",
            "CANCELLED": "Cancelled",
        }[self.value]


# --------------------------------------------------------------------------- params
class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoParams(_Params):
    pass


class ProcessParams(_Params):
    pid: int = Field(gt=4)
    create_time: float = Field(ge=0)
    name: str = Field(min_length=1, max_length=260)
    force_if_unresponsive: bool = False


class StartupParams(_Params):
    item_id: str = Field(min_length=3, max_length=600)


class CleanupParams(_Params):
    target_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class PowerPlanParams(_Params):
    guid: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ServiceParams(_Params):
    name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.\-]+$")


# ----------------------------------------------------------------------- requests
class ActionRequest(BaseModel):
    action_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "rule"          # "rule" | "ai" | "user"
    issue_key: str | None = None
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(slots=True)
class Approval:
    """Proof that the user explicitly approved *this* request (created only by the UI/CLI confirm step)."""

    request_id: str
    approved_at: float = field(default_factory=time.time)
    acknowledged_warnings: bool = True


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_admin: bool = False
    params: BaseModel | None = None
    status: ResultStatus | None = None   # REJECTED (unknown/malformed) or BLOCKED (safety)


@dataclass(slots=True)
class Verification:
    status: ResultStatus
    summary: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    improvement: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionResult:
    """What a handler returns from ``execute``."""

    success: bool
    message: str
    new_state: dict[str, Any] = field(default_factory=dict)
    partial: bool = False


@dataclass(slots=True)
class ActionOutcome:
    request_id: str
    action_id: str
    target: str
    status: ResultStatus
    message: str
    verification: Verification | None = None
    previous_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    rollback_available: bool = False
    record_id: int | None = None
