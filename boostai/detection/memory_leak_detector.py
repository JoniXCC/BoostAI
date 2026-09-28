"""Possible-memory-leak detection.

A high-memory process is *not* a leak. BoostAI reports a *possible* leak only when a
single process instance's private (committed) memory shows sustained growth:

* observed long enough (``leak_min_minutes``) with enough samples;
* absolute growth >= ``leak_min_growth_mb`` and relative growth >= ``leak_min_relative_growth``;
* a positive least-squares slope that explains the data well (R^2);
* most steps are non-decreasing (growth is persistent, not a single jump);
* little memory is ever released (small drawdown relative to growth).

Evidence strength is converted into LOW / MEDIUM / HIGH *confidence*. The report never
claims a definite leak: normal caching, new tabs or new documents also grow memory.
"""

from __future__ import annotations

from dataclasses import dataclass

from boostai.config.settings import DetectionSettings
from boostai.core.issues import Confidence, Issue, IssueType, RootCause, Severity
from boostai.core.models import GB, MB, ProcessInfo, fmt_bytes, fmt_duration
from boostai.core.trend import TrendAnalysis, analyze_trend
from boostai.detection.base import DetectionContext, restart_or_close_proposals, root_instance

EXCLUDED = frozenset({"system", "registry", "memcompression", "memory compression", "secure system", "vmmem", "vmmemwsl"})
MIN_SAMPLES = 6


@dataclass(slots=True)
class LeakAssessment:
    confidence: Confidence
    score: int
    reasons: list[str]
    minutes_to_exhaust_available: float | None


def evaluate_leak(
    trend: TrendAnalysis, settings: DetectionSettings, available_bytes: int, baseline_p95: float | None = None
) -> LeakAssessment | None:
    """Pure decision function (unit-tested). Returns None when the evidence is insufficient."""
    minutes = trend.duration_s / 60.0
    if trend.samples < MIN_SAMPLES or minutes < settings.leak_min_minutes:
        return None
    if trend.growth < settings.leak_min_growth_mb * MB or trend.relative_growth < settings.leak_min_relative_growth:
        return None
    if trend.slope_per_min <= 0 or trend.r_squared < 0.5 or trend.non_decreasing_ratio < 0.6:
        return None
    if trend.release_ratio > 0.35:
        return None  # it regularly gives memory back - normal fluctuation, not a leak pattern

    score = 0
    reasons: list[str] = []
    if trend.r_squared >= 0.9:
        score += 2
        reasons.append("growth is very steady (linear fit R² ≥ 0.9)")
    elif trend.r_squared >= 0.75:
        score += 1
        reasons.append("growth is fairly steady")
    if trend.non_decreasing_ratio >= 0.8:
        score += 2
        reasons.append(f"{trend.non_decreasing_ratio:.0%} of samples did not decrease")
    elif trend.non_decreasing_ratio >= 0.65:
        score += 1
    if trend.release_ratio <= 0.05:
        score += 2
        reasons.append("no meaningful memory release was observed")
    elif trend.release_ratio <= 0.15:
        score += 1
        reasons.append("only minor memory release was observed")
    if minutes >= 60:
        score += 2
    elif minutes >= 30:
        score += 1
    if trend.growth >= 1 * GB:
        score += 1
    if baseline_p95 and trend.end_value > baseline_p95 * 1.5:
        score += 1
        reasons.append("usage is well above this app's usual peak on this PC")

    confidence = Confidence.HIGH if score >= 7 else Confidence.MEDIUM if score >= 4 else Confidence.LOW
    exhaust = available_bytes / (trend.slope_per_min) if trend.slope_per_min > 0 else None
    return LeakAssessment(confidence, score, reasons, exhaust)


def leak_severity(current: float, total_ram: int, minutes_to_exhaust: float | None, confidence: Confidence) -> Severity:
    share = current / total_ram if total_ram else 0.0
    if minutes_to_exhaust is not None and minutes_to_exhaust < 60 and confidence != Confidence.LOW:
        return Severity.HIGH
    if share >= 0.25 and confidence != Confidence.LOW:
        return Severity.HIGH
    if share >= 0.10 or confidence == Confidence.HIGH:
        return Severity.MEDIUM
    return Severity.LOW


def detect(ctx: DetectionContext) -> list[Issue]:
    issues: list[Issue] = []
    live = {p.key: p for p in ctx.snapshot.processes}
    noise = 2 * MB
    for series in ctx.history.instance_series():
        if series.name.lower() in EXCLUDED:
            continue
        key = (series.pid, round(series.create_time, 3))
        proc: ProcessInfo | None = live.get(key)
        if proc is None:
            continue
        trend = analyze_trend([(t, float(priv)) for t, priv, *_ in series.points], noise)
        if trend is None:
            continue
        baseline = ctx.baselines.process(series.name, "memory")
        assessment = evaluate_leak(
            trend, ctx.settings, ctx.snapshot.memory.available,
            baseline.p95 if baseline and baseline.reliable else None,
        )
        if assessment is None:
            continue
        issues.append(_make_issue(ctx, proc, trend, assessment))
    return issues


def detect_buildup(ctx: DetectionContext, already_flagged: set[str]) -> list[Issue]:
    """App-level RAM build-up across *all* instances of a multi-process app (e.g. browsers).

    Weaker evidence than a per-instance leak (child processes come and go), so confidence
    is capped at MEDIUM and the wording says 'build-up', not 'leak'.
    """
    issues: list[Issue] = []
    for lname in ctx.history.group_names():
        if lname in EXCLUDED or lname in already_flagged:
            continue
        series = ctx.history.group_series(lname)
        trend = analyze_trend([(t, float(priv)) for t, priv, _cpu, _n in series], 5 * MB)
        if trend is None or trend.duration_s < 20 * 60 or trend.samples < MIN_SAMPLES:
            continue
        if trend.growth < 500 * MB or trend.relative_growth < 0.5 or trend.r_squared < 0.7 or trend.release_ratio > 0.3:
            continue
        instances = ctx.processes_named(lname)
        if not instances:
            continue
        root = root_instance(instances)
        confidence = Confidence.MEDIUM if trend.r_squared >= 0.9 and trend.release_ratio <= 0.1 else Confidence.LOW
        name = ctx.history.display_name(lname)
        game = ctx.is_game(root)
        issues.append(Issue(
            key=f"{IssueType.MEMORY_BUILDUP.value}:{lname}",
            type=IssueType.MEMORY_BUILDUP,
            title=f"Memory build-up: {name}",
            severity=Severity.MEDIUM if trend.end_value >= 0.15 * ctx.total_ram else Severity.LOW,
            confidence=confidence,
            root_cause=RootCause.UNKNOWN,
            component=f"{name} ({len(instances)} processes)",
            evidence=[
                f"Combined private memory of all {name} processes grew from {fmt_bytes(trend.start_value)} to "
                f"{fmt_bytes(trend.end_value)} over {fmt_duration(trend.duration_s)}.",
                f"Steadiness of growth: R² = {trend.r_squared:.2f}; largest release {fmt_bytes(trend.max_release)}.",
            ],
            metrics={"process": name, "growth_bytes": int(trend.growth), "current_bytes": int(trend.end_value),
                     "observed_minutes": round(trend.duration_s / 60, 1), "instances": len(instances)},
            explanation=(
                f"{name} has been accumulating memory across its processes. This can be a leak, or simply more open "
                "tabs/documents/content. Closing unused tabs or restarting the application usually releases it."
            ),
            proposals=[] if game else restart_or_close_proposals(root, ctx, close=False),
            manual_steps=["Close tabs, documents or projects you no longer need in this application."],
            expected_effect="Restarting usually returns the application to its starting memory usage.",
        ))
    return issues


def _make_issue(ctx: DetectionContext, proc: ProcessInfo, trend: TrendAnalysis, a: LeakAssessment) -> Issue:
    minutes = trend.duration_s / 60
    severity = leak_severity(trend.end_value, ctx.total_ram, a.minutes_to_exhaust_available, a.confidence)
    evidence = [
        f"Private memory grew from {fmt_bytes(trend.start_value)} to {fmt_bytes(trend.end_value)} "
        f"(+{fmt_bytes(trend.growth)}) over {fmt_duration(trend.duration_s)}.",
        f"Average growth rate: {fmt_bytes(trend.slope_per_min)}/min across {trend.samples} samples "
        f"(fit R² = {trend.r_squared:.2f}).",
        f"Largest release during the window: {fmt_bytes(trend.max_release)}.",
    ]
    if a.minutes_to_exhaust_available is not None and a.minutes_to_exhaust_available < 240:
        evidence.append(
            f"At this rate the currently available RAM ({fmt_bytes(ctx.snapshot.memory.available)}) "
            f"would be consumed in about {a.minutes_to_exhaust_available:.0f} minutes."
        )
    reason = "; ".join(a.reasons) or "sustained growth across the observation window"
    game = ctx.is_game(proc)
    proposals = [] if game else restart_or_close_proposals(
        proc, ctx, close=False, reason="Restarting the process is likely to release the accumulated memory.")
    manual = []
    verdict = ctx.protection(proc)
    if verdict.protected:
        manual.append(f"BoostAI will not act on this process ({'; '.join(verdict.reasons)}).")
    if game:
        manual.append("This looks like a game. Restart it between sessions if memory keeps climbing.")
    manual.append("If this keeps happening, update the application; persistent growth is often fixed in newer versions.")
    return Issue(
        key=f"{IssueType.MEMORY_LEAK.value}:{proc.name.lower()}:{proc.pid}",
        type=IssueType.MEMORY_LEAK,
        title=f"Possible memory leak: {proc.name}",
        severity=severity,
        confidence=a.confidence,
        root_cause=RootCause.POSSIBLE_MEMORY_LEAK,
        component=f"{proc.name} (PID {proc.pid})",
        evidence=evidence,
        metrics={
            "process": proc.name, "pid": proc.pid, "current_bytes": int(trend.end_value),
            "start_bytes": int(trend.start_value), "growth_bytes": int(trend.growth),
            "observed_minutes": round(minutes, 1), "slope_bytes_per_min": round(trend.slope_per_min),
            "r_squared": round(trend.r_squared, 3), "score": a.score,
        },
        explanation=(
            f"Memory usage has increased consistently across the observation window without significant release "
            f"({reason}). This pattern is consistent with a memory leak, but it can also be caused by legitimate "
            f"growth such as opening more tabs or documents, so it is reported as a possibility."
        ),
        proposals=proposals,
        manual_steps=manual,
        expected_effect=f"Restarting typically returns the process to its starting size (~{fmt_bytes(trend.start_value)}).",
    )
