import random

from boostai.config.settings import DetectionSettings
from boostai.core.baselines import Baseline, BaselineEngine
from boostai.core.history import History
from boostai.core.issues import Confidence, IssueType, Severity
from boostai.core.models import GB, MB
from boostai.core.trend import analyze_trend
from boostai.detection.base import DetectionContext
from boostai.detection.memory_leak_detector import detect, detect_buildup, evaluate_leak, leak_severity
from tests.fakes import proc, snapshot, system_info

S = DetectionSettings()
T0 = 1_000_000.0


def series(start_mb, per_min_mb, minutes, step_s=60, noise_mb=0.0, seed=1):
    rnd = random.Random(seed)
    n = int(minutes * 60 / step_s) + 1
    return [(T0 + i * step_s, (start_mb + per_min_mb * i * step_s / 60 + rnd.uniform(-noise_mb, noise_mb)) * MB)
            for i in range(n)]


def test_steady_long_growth_is_high_confidence():
    trend = analyze_trend(series(900, 80, 45), 2 * MB)
    a = evaluate_leak(trend, S, available_bytes=4 * GB)
    assert a is not None and a.confidence == Confidence.HIGH


def test_short_observation_is_not_reported():
    trend = analyze_trend(series(900, 100, 5), 2 * MB)
    assert evaluate_leak(trend, S, 4 * GB) is None


def test_small_growth_is_not_reported():
    trend = analyze_trend(series(900, 3, 40), 2 * MB)  # +120 MB < 200 MB threshold
    assert evaluate_leak(trend, S, 4 * GB) is None


def test_high_but_flat_memory_is_not_a_leak():
    trend = analyze_trend(series(6000, 0, 60, noise_mb=40), 2 * MB)
    assert evaluate_leak(trend, S, 4 * GB) is None


def test_growth_with_large_releases_is_not_a_leak():
    pts = []
    for i in range(60):  # sawtooth: grows 500 MB then drops back, repeatedly
        pts.append((T0 + i * 60, (1000 + (i % 10) * 60) * MB))
    trend = analyze_trend(pts, 2 * MB)
    assert evaluate_leak(trend, S, 4 * GB) is None


def test_noisy_growth_gets_lower_confidence_than_clean_growth():
    clean = evaluate_leak(analyze_trend(series(500, 20, 20), 2 * MB), S, 8 * GB)
    noisy = evaluate_leak(analyze_trend(series(500, 20, 20, noise_mb=60, seed=3), 2 * MB), S, 8 * GB)
    assert clean is not None
    assert noisy is None or noisy.confidence.rank <= clean.confidence.rank


def test_baseline_increases_score():
    trend = analyze_trend(series(500, 20, 20), 2 * MB)
    without = evaluate_leak(trend, S, 8 * GB)
    with_base = evaluate_leak(trend, S, 8 * GB, baseline_p95=300 * MB)
    assert with_base.score == without.score + 1


def test_leak_severity():
    assert leak_severity(5 * GB, 16 * GB, 30, Confidence.MEDIUM) == Severity.HIGH
    assert leak_severity(2 * GB, 16 * GB, None, Confidence.LOW) == Severity.MEDIUM
    assert leak_severity(500 * MB, 16 * GB, None, Confidence.LOW) == Severity.LOW
    assert leak_severity(500 * MB, 16 * GB, None, Confidence.HIGH) == Severity.MEDIUM


def _ctx(history, processes, **kw):
    return DetectionContext(snapshot=snapshot(processes), system_info=system_info(), history=history,
                            baselines=BaselineEngine(None), settings=S, self_pid=1, user="TESTPC\\alice",
                            now=T0 + 3600, **kw)


def _feed(history, p, pts):
    for t, v in pts:
        p.private = int(v)
        history.add_processes(t, [p])


def test_detect_creates_issue_with_evidence_and_restart_proposal():
    h = History(history_minutes=120, process_interval_s=60)
    p = proc(2000, "leaky.exe")
    _feed(h, p, series(900, 80, 45))
    issues = detect(_ctx(h, [p]))
    assert len(issues) == 1
    issue = issues[0]
    assert issue.type == IssueType.MEMORY_LEAK and issue.title.startswith("Possible memory leak")
    assert "grew from" in issue.evidence[0]
    assert [pr.action_id for pr in issue.proposals] == ["RESTART_PROCESS"]


def test_detect_offers_no_action_for_protected_process():
    h = History(history_minutes=120, process_interval_s=60)
    p = proc(2000, "svchost.exe", user="NT AUTHORITY\\SYSTEM", session=0, exe="C:\\Windows\\System32\\svchost.exe")
    _feed(h, p, series(900, 80, 45))
    issues = detect(_ctx(h, [p]))
    assert issues and issues[0].proposals == []
    assert any("will not act" in m for m in issues[0].manual_steps)


def test_group_buildup_is_capped_below_high_confidence():
    h = History(history_minutes=120, process_interval_s=60)
    for i, (t, v) in enumerate(series(1000, 40, 40)):
        a = proc(10, "browser.exe", private=int(v * 0.6), ppid=1)
        b = proc(11 + i, "browser.exe", private=int(v * 0.4), ppid=10, create_time=T0 + i)  # children churn
        h.add_processes(t, [a, b])
    issues = detect_buildup(_ctx(h, [a, b]), set())
    assert issues and issues[0].type == IssueType.MEMORY_BUILDUP
    assert issues[0].confidence != Confidence.HIGH


def test_baseline_engine_reliability():
    b = BaselineEngine(None)
    b.set("discord.exe", "memory", Baseline(500 * MB, 700 * MB, 520 * MB, 10))
    assert not b.process("Discord.exe").reliable
