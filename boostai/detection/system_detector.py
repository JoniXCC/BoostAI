"""Temperature and power-plan checks."""

from __future__ import annotations

from boostai.actions.action_models import ActionID
from boostai.core.issues import ActionProposal, Confidence, Issue, IssueType, RootCause, Severity
from boostai.detection.base import DetectionContext
from boostai.metrics.power import BALANCED, POWER_SAVER


def detect(ctx: DetectionContext) -> list[Issue]:
    issues: list[Issue] = []
    limit = ctx.settings.gpu_temp_warn_c
    for r in ctx.snapshot.temperatures.readings:
        if r.celsius >= limit:
            issues.append(Issue(
                key=f"{IssueType.OVERHEATING.value}:{r.sensor}", type=IssueType.OVERHEATING,
                title=f"High temperature: {r.sensor} at {r.celsius:.0f} °C",
                severity=Severity.HIGH if r.celsius >= limit + 5 else Severity.MEDIUM, confidence=Confidence.HIGH,
                root_cause=RootCause.OVERHEATING, component=r.sensor,
                evidence=[f"{r.sensor} reads {r.celsius:.0f} °C (source: {r.source}); warning threshold "
                          f"{limit:.0f} °C."],
                metrics={"celsius": r.celsius},
                explanation=("Hot components throttle their clock speed, which lowers performance. BoostAI never "
                             "changes fan curves, voltages or clocks."),
                manual_steps=["Make sure air vents are not blocked; use the laptop on a hard, flat surface.",
                              "Clean dust from fans and heatsinks.",
                              "If temperatures stay high at light load, have the cooling system serviced."],
            ))

    active = next((p for p in ctx.power_plans if p.active), None)
    balanced = next((p for p in ctx.power_plans if p.guid == BALANCED), None)
    if active and active.guid == POWER_SAVER and not ctx.on_battery and balanced:
        issues.append(Issue(
            key=IssueType.POWER_PLAN.value, type=IssueType.POWER_PLAN,
            title="Power saver plan active while plugged in", severity=Severity.LOW, confidence=Confidence.HIGH,
            root_cause=RootCause.CONFIGURATION, component="Windows power plan",
            evidence=[f"Active plan: {active.name}. The PC is on AC power."],
            explanation="Power saver limits CPU performance to save energy, which is unnecessary when plugged in.",
            proposals=[ActionProposal(action_id=ActionID.CHANGE_POWER_PLAN.value, params={"guid": balanced.guid},
                                      label=f"Switch to '{balanced.name}'",
                                      rationale="Balanced gives full performance on demand and saves power at idle.")],
            expected_effect="Higher CPU performance under load; reversible at any time.",
        ))
    return issues
