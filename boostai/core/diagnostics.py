"""Runs every detector, enriches issues with action metadata and computes the health score."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from boostai.actions.registry import ActionRegistry
from boostai.core.issues import Confidence, Issue, IssueType, sort_issues
from boostai.core.performance_score import HealthScore, ScoreInputs, compute_score
from boostai.core.trend import mean
from boostai.detection import (
    anomaly_detector,
    cpu_detector,
    disk_detector,
    memory_leak_detector,
    memory_pressure_detector,
    service_detector,
    startup_detector,
    system_detector,
)
from boostai.detection.base import DetectionContext
from boostai.knowledge.startup_catalog import StartupCategory, assess

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DiagnosticReport:
    issues: list[Issue]
    score: HealthScore


class DiagnosticsEngine:
    def __init__(self, registry: ActionRegistry) -> None:
        self.registry = registry

    def run(self, ctx: DetectionContext) -> DiagnosticReport:
        issues: list[Issue] = []

        def safe(name, fn, *args):
            try:
                return fn(*args)
            except Exception:  # one failing detector must never break the scan
                log.exception("Detector %s failed", name)
                return []

        leaks = safe("memory_leak", memory_leak_detector.detect, ctx)
        leak_names = {str(i.metrics.get("process", "")).lower() for i in leaks}
        issues += leaks
        buildup = safe("memory_buildup", memory_leak_detector.detect_buildup, ctx, leak_names)
        issues += buildup
        flagged = leak_names | {str(i.metrics.get("process", "")).lower() for i in buildup}
        issues += safe("memory_pressure", memory_pressure_detector.detect, ctx, leak_names)
        issues += safe("cpu", cpu_detector.detect, ctx)
        issues += safe("disk", disk_detector.detect, ctx)
        issues += safe("startup", startup_detector.detect, ctx)
        issues += safe("services", service_detector.detect, ctx)
        issues += safe("anomaly", anomaly_detector.detect, ctx, flagged)
        issues += safe("system", system_detector.detect, ctx)

        unique: dict[str, Issue] = {}
        for issue in issues:
            unique.setdefault(issue.key, issue)
        enriched = [self._enrich(i) for i in unique.values()]
        return DiagnosticReport(issues=sort_issues(enriched), score=self.score(ctx, enriched))

    def _enrich(self, issue: Issue) -> Issue:
        """Fill risk / reversibility from the whitelisted action definitions (never from free text)."""
        valid = [p for p in issue.proposals if self.registry.is_known(p.action_id)]
        issue.proposals = valid
        if valid:
            d = self.registry.definition(valid[0].action_id)
            issue.risk = d.risk.value.title()
            issue.reversible = d.reversibility.label
            if not issue.expected_effect:
                issue.expected_effect = d.description
            if valid[0].action_id in ("CLEAR_SAFE_TEMP_FILES", "CLEAR_SPECIFIC_SAFE_CACHE"):
                issue.files_affected = issue.files_affected if issue.files_affected != "None" else \
                    "Only files inside the approved temporary/cache folders"
        else:
            issue.risk = "n/a (manual action)"
            issue.reversible = "Not applicable"
        return issue

    @staticmethod
    def score(ctx: DetectionContext, issues: list[Issue]) -> HealthScore:
        snap = ctx.snapshot
        recent = ctx.history.system_points(since=ctx.now - 120)
        cpu_avg = mean([p.cpu for p in recent]) if len(recent) >= 3 else snap.cpu.total_percent
        irq = mean([p.interrupt_dpc for p in recent]) if len(recent) >= 3 else (
            snap.cpu.interrupt_percent + snap.cpu.dpc_percent)
        idle_points = [p.cpu for p in ctx.history.system_points(since=ctx.now - 900)
                       if p.user_idle is not None and p.user_idle >= ctx.settings.user_idle_seconds]
        busy = [p.disk_busy for p in recent if p.disk_busy is not None]
        faults = [p.hard_faults for p in recent if p.hard_faults is not None]
        enabled = [i for i in ctx.startup_items if i.enabled]
        cats = [assess(i, snap.processes).category for i in enabled]
        sysdisk = snap.system_disk
        base = ctx.baselines.system("process_count")
        leaks = sum(1 for i in issues if i.type == IssueType.MEMORY_LEAK and i.confidence != Confidence.LOW)
        inputs = ScoreInputs(
            ram_percent=snap.memory.percent,
            commit_percent=snap.memory.commit_percent,
            hard_faults_per_sec=mean(faults) if len(faults) >= 3 else snap.memory.hard_faults_per_sec,
            possible_leaks=leaks,
            cpu_avg_percent=cpu_avg,
            idle_cpu_percent=mean(idle_points) if len(idle_points) >= 10 else None,
            interrupt_dpc_percent=irq,
            startup_high_impact=cats.count(StartupCategory.HIGH_IMPACT),
            startup_optional=cats.count(StartupCategory.OPTIONAL),
            startup_unknown=cats.count(StartupCategory.UNKNOWN),
            startup_known=bool(ctx.startup_items),
            system_disk_free_percent=(100 - sysdisk.percent) if sysdisk else None,
            system_disk_free_gb=(sysdisk.free / 1024**3) if sysdisk else None,
            disk_busy_percent=mean(busy) if busy else snap.disk_activity.busy_percent,
            disk_latency_ms=snap.disk_activity.avg_latency_ms,
            process_count=snap.process_count,
            process_count_baseline=base.median if base and base.reliable else None,
        )
        return compute_score(inputs)
