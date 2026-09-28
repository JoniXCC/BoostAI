"""Issue model shared by detectors, the diagnostics engine, the UI and the AI advisor."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"].index(self.value)


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        return ["LOW", "MEDIUM", "HIGH"].index(self.value)


class RootCause(str, Enum):
    """What kind of problem this is - BoostAI never pretends everything is a software fix."""

    SOFTWARE_ISSUE = "Software issue"
    POSSIBLE_MEMORY_LEAK = "Possible memory leak"
    WORKLOAD = "High workload (many programs open)"
    CONFIGURATION = "Configuration"
    HARDWARE_LIMITATION = "Hardware limitation"
    INSUFFICIENT_RAM = "Insufficient RAM"
    OVERHEATING = "Overheating"
    FAILING_STORAGE = "Failing storage"
    DRIVER_ISSUE = "Possible driver issue"
    MALWARE_SUSPICION = "Unusual behaviour (not a malware verdict)"
    UNKNOWN = "Unknown cause"


class IssueType(str, Enum):
    MEMORY_LEAK = "MEMORY_LEAK"
    MEMORY_BUILDUP = "MEMORY_BUILDUP"
    MEMORY_PRESSURE = "MEMORY_PRESSURE"
    HIGH_COMMIT = "HIGH_COMMIT"
    HEAVY_PAGING = "HEAVY_PAGING"
    SUSTAINED_HIGH_CPU = "SUSTAINED_HIGH_CPU"
    CPU_MONOPOLY = "CPU_MONOPOLY"
    HIGH_IDLE_CPU = "HIGH_IDLE_CPU"
    CPU_SPIKES = "CPU_SPIKES"
    HIGH_INTERRUPT_CPU = "HIGH_INTERRUPT_CPU"
    LOW_DISK_SPACE = "LOW_DISK_SPACE"
    DISK_BUSY = "DISK_BUSY"
    STORAGE_HEALTH = "STORAGE_HEALTH"
    SLOW_SYSTEM_DRIVE = "SLOW_SYSTEM_DRIVE"
    STARTUP_LOAD = "STARTUP_LOAD"
    SERVICE_RESOURCE_USE = "SERVICE_RESOURCE_USE"
    PROCESS_ABOVE_BASELINE = "PROCESS_ABOVE_BASELINE"
    EXCESSIVE_PROCESSES = "EXCESSIVE_PROCESSES"
    UNUSUAL_PROCESS_BEHAVIOR = "UNUSUAL_PROCESS_BEHAVIOR"
    OVERHEATING = "OVERHEATING"
    POWER_PLAN = "POWER_PLAN"
    TEMP_FILES = "TEMP_FILES"


class ActionProposal(BaseModel):
    """A *suggestion* to run a whitelisted action. It is never executed without validation and approval."""

    action_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    label: str
    rationale: str = ""
    warning: str | None = None


class Issue(BaseModel):
    key: str                                 # stable de-duplication key, e.g. "MEMORY_LEAK:chrome.exe"
    type: IssueType
    title: str
    severity: Severity
    confidence: Confidence
    root_cause: RootCause
    component: str
    evidence: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    explanation: str
    proposals: list[ActionProposal] = Field(default_factory=list)
    manual_steps: list[str] = Field(default_factory=list)
    risk: str = "n/a"
    expected_effect: str = ""
    reversible: str = "Not applicable"
    files_affected: str = "None"
    detected_at: float = Field(default_factory=time.time)
    status: str = "open"
    db_id: int | None = None

    @property
    def auto_fixable(self) -> bool:
        return bool(self.proposals)


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (i.severity.rank, i.confidence.rank), reverse=True)
