"""Services using notable resources. Actions are offered only for allow-listed optional services."""

from __future__ import annotations

from collections import defaultdict

from boostai.actions.action_models import ActionID
from boostai.core.issues import ActionProposal, Confidence, Issue, IssueType, RootCause, Severity
from boostai.core.models import MB, fmt_bytes
from boostai.core.trend import mean
from boostai.detection.base import DetectionContext
from boostai.security.protected_services import ServiceClass, classify_service, optional_service

CPU_THRESHOLD = 10.0      # % of total CPU, sustained
MEMORY_THRESHOLD = 800 * MB


def detect(ctx: DetectionContext) -> list[Issue]:
    by_pid: dict[int, list] = defaultdict(list)
    for svc in ctx.services:
        if svc.pid and svc.status == "running":
            by_pid[svc.pid].append(svc)
    issues: list[Issue] = []
    since = ctx.now - ctx.settings.cpu_sustained_minutes * 60
    for proc in ctx.snapshot.processes:
        hosted = by_pid.get(proc.pid)
        if not hosted:
            continue
        cpu_values = ctx.history.cpu_window(proc.key, since)
        avg_cpu = mean(cpu_values) if len(cpu_values) >= 3 else 0.0
        if avg_cpu < CPU_THRESHOLD and proc.private < MEMORY_THRESHOLD:
            continue
        names = ", ".join(s.display_name or s.name for s in hosted)
        evidence = [f"Host process {proc.name} (PID {proc.pid}) runs: {names}."]
        if avg_cpu >= CPU_THRESHOLD:
            evidence.append(f"Averaged {avg_cpu:.0f}% of total CPU over the last {ctx.settings.cpu_sustained_minutes:.0f} minutes.")
        if proc.private >= MEMORY_THRESHOLD:
            evidence.append(f"Private memory: {fmt_bytes(proc.private)}.")

        proposals: list[ActionProposal] = []
        manual: list[str] = []
        classes = [classify_service(s.name) for s in hosted]
        if len(hosted) == 1 and classes[0][0] == ServiceClass.OPTIONAL:
            svc = hosted[0]
            opt = optional_service(svc.name)
            if opt and opt.restartable:
                proposals.append(ActionProposal(
                    action_id=ActionID.RESTART_SERVICE.value, params={"name": svc.name},
                    label=f"Restart service {svc.display_name or svc.name}", rationale=opt.description))
            if opt and opt.stoppable:
                proposals.append(ActionProposal(
                    action_id=ActionID.STOP_OPTIONAL_SERVICE.value, params={"name": svc.name},
                    label=f"Stop service {svc.display_name or svc.name} until next restart", rationale=opt.description))
            cause, explanation = RootCause.SOFTWARE_ISSUE, "An optional Windows service is using notable resources."
        elif any(c == ServiceClass.PROTECTED for c, _ in classes):
            cause = RootCause.WORKLOAD
            explanation = ("A protected Windows component is busy. This is usually temporary (updates, security "
                           "scans, indexing) and BoostAI will not stop it.")
            manual.append("Wait for the activity to finish. If it persists for hours, check Windows Update status.")
        else:
            cause = RootCause.UNKNOWN
            explanation = "A service that BoostAI does not recognise is using notable resources. Manual review required."
            manual.append("Check which application installed this service and whether you still need it.")
        issues.append(Issue(
            key=f"{IssueType.SERVICE_RESOURCE_USE.value}:{proc.pid}",
            type=IssueType.SERVICE_RESOURCE_USE,
            title=f"Service using resources: {hosted[0].display_name or hosted[0].name}"
                  + (f" (+{len(hosted) - 1} more)" if len(hosted) > 1 else ""),
            severity=Severity.LOW if avg_cpu < 25 else Severity.MEDIUM,
            confidence=Confidence.MEDIUM, root_cause=cause, component=f"Windows service host PID {proc.pid}",
            evidence=evidence,
            metrics={"services": [s.name for s in hosted], "cpu_percent": avg_cpu, "private_bytes": proc.private},
            explanation=explanation, proposals=proposals, manual_steps=manual,
        ))
    return issues
