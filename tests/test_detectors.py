"""Severity and context logic of the non-leak detectors."""

from boostai.config.settings import DetectionSettings
from boostai.core.baselines import Baseline, BaselineEngine
from boostai.core.comparison import MeasuredState, compare
from boostai.core.history import History, SystemPoint
from boostai.core.issues import IssueType, RootCause, Severity
from boostai.core.models import GB, MB, PowerPlan, PressureLevel
from boostai.detection import anomaly_detector, cpu_detector, disk_detector, memory_pressure_detector, system_detector
from boostai.detection.base import DetectionContext
from tests.fakes import proc, snapshot, system_info

NOW = 2_000_000.0


def ctx_for(snap, history=None, baselines=None, **kw):
    return DetectionContext(snapshot=snap, system_info=system_info(snap.memory.total), history=history or History(),
                            baselines=baselines or BaselineEngine(None), settings=DetectionSettings(), self_pid=1,
                            user="TESTPC\\alice", now=NOW, **kw)


def test_pressure_many_apps_is_workload_not_leak():
    procs = [proc(10 + i, f"app{i}.exe", private=1500 * MB) for i in range(8)]
    snap = snapshot(procs, ram_percent=94, pressure=PressureLevel.HIGH)
    issues = memory_pressure_detector.detect(ctx_for(snap), set())
    issue = next(i for i in issues if i.type == IssueType.MEMORY_PRESSURE)
    assert issue.root_cause == RootCause.WORKLOAD and "No single memory leak" in issue.explanation
    assert issue.severity == Severity.HIGH
    assert all(p.action_id == "CLOSE_USER_PROCESS" for p in issue.proposals) and len(issue.proposals) <= 3


def test_pressure_attributes_leak_when_present():
    procs = [proc(10, "leaky.exe", private=9 * GB), proc(11, "b.exe", private=1 * GB)]
    snap = snapshot(procs, ram_percent=96, pressure=PressureLevel.CRITICAL)
    issue = memory_pressure_detector.detect(ctx_for(snap), {"leaky.exe"})[0]
    assert issue.root_cause == RootCause.POSSIBLE_MEMORY_LEAK and issue.severity == Severity.CRITICAL


def test_foreground_app_not_proposed_for_closing():
    procs = [proc(10, "game.exe", private=6 * GB, foreground=True), proc(11, "chat.exe", private=1 * GB)]
    snap = snapshot(procs, ram_percent=93, pressure=PressureLevel.HIGH)
    issue = memory_pressure_detector.detect(ctx_for(snap), set())[0]
    assert all(p.params["name"] != "game.exe" for p in issue.proposals)


def test_small_ram_persistent_pressure_is_insufficient_ram():
    h = History()
    for i in range(40):
        h.add_system(SystemPoint(NOW - 1700 + i * 40, 20, 92, 1, 80, 0, 0, 10, 100, 0, 0, 100))
    procs = [proc(10 + i, f"a{i}.exe", private=500 * MB) for i in range(10)]
    snap = snapshot(procs, ram_percent=92, total=8 * GB, pressure=PressureLevel.HIGH)
    issue = memory_pressure_detector.detect(ctx_for(snap, h), set())[0]
    assert issue.root_cause == RootCause.INSUFFICIENT_RAM


def _cpu_history(game: bool):
    h = History(process_interval_s=10, system_interval_s=10)
    p = proc(50, "game.exe" if game else "miner.exe", cpu=95.0, foreground=game,
             exe="D:\\SteamLibrary\\steamapps\\common\\Game\\game.exe" if game else "C:\\Tools\\miner.exe")
    for i in range(30):
        t = NOW - 290 + i * 10
        h.add_system(SystemPoint(t, 96, 50, 8 * GB, 50, 0, 0, 5, 100, 0, 1, 10))
        h.add_processes(t, [p])
    return h, p


def test_game_cpu_load_is_info_not_problem():
    h, p = _cpu_history(game=True)
    issues = cpu_detector.detect(ctx_for(snapshot([p], cpu=96), h, fullscreen_foreground=True))
    sustained = next(i for i in issues if i.type == IssueType.SUSTAINED_HIGH_CPU)
    assert sustained.severity == Severity.INFO and sustained.root_cause == RootCause.WORKLOAD
    assert not any(i.type == IssueType.CPU_MONOPOLY for i in issues)


def test_background_cpu_hog_is_flagged_with_actions():
    h, p = _cpu_history(game=False)
    issues = cpu_detector.detect(ctx_for(snapshot([p], cpu=96), h))
    mono = next(i for i in issues if i.type == IssueType.CPU_MONOPOLY)
    assert {pr.action_id for pr in mono.proposals} == {"RESTART_PROCESS", "CLOSE_USER_PROCESS"}


def test_interrupt_time_is_driver_issue():
    h = History(system_interval_s=2)
    for i in range(30):
        h.add_system(SystemPoint(NOW - 60 + i * 2, 30, 50, 8 * GB, 50, 0, 0, 5, 100, 0, 15.0, 10))
    issues = cpu_detector.detect(ctx_for(snapshot(), h))
    assert any(i.type == IssueType.HIGH_INTERRUPT_CPU and i.root_cause == RootCause.DRIVER_ISSUE for i in issues)


def test_low_disk_space_severity():
    issues = disk_detector.detect(ctx_for(snapshot(free_percent=3)))
    low = next(i for i in issues if i.type == IssueType.LOW_DISK_SPACE)
    assert low.severity == Severity.CRITICAL
    assert disk_detector.detect(ctx_for(snapshot(free_percent=40))) == []


def test_failing_storage_is_hardware_issue():
    from boostai.core.models import StorageDevice

    c = ctx_for(snapshot())
    c.system_info.storage = [StorageDevice("Disk0", "HDD", "SATA", "Unhealthy")]
    issues = disk_detector.detect(c)
    assert any(i.root_cause == RootCause.FAILING_STORAGE and i.severity == Severity.CRITICAL for i in issues)
    assert any(i.type == IssueType.SLOW_SYSTEM_DRIVE for i in issues)


def test_above_baseline_wording():
    b = BaselineEngine(None)
    b.set("discord.exe", "memory", Baseline(550 * MB, 700 * MB, 560 * MB, 500))
    snap = snapshot([proc(10, "Discord.exe", private=int(2.8 * GB))])
    issue = anomaly_detector.detect(ctx_for(snap, baselines=b), set())[0]
    assert "significantly more memory than its recent baseline" in issue.explanation
    assert issue.severity == Severity.MEDIUM


def test_overheating_and_power_saver():
    from boostai.core.models import TemperatureReading

    snap = snapshot()
    snap.temperatures.readings = [TemperatureReading("GPU", 93, "NVML")]
    plans = [PowerPlan("a1841308-3541-4fab-bc81-f71556f20b4a", "Power saver", True),
             PowerPlan("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced", False)]
    issues = system_detector.detect(ctx_for(snap, power_plans=plans))
    assert {i.type for i in issues} == {IssueType.OVERHEATING, IssueType.POWER_PLAN}
    assert next(i for i in issues if i.type == IssueType.OVERHEATING).proposals == []


def ms(ram_used, cpu, procs, pressure):
    return MeasuredState(0, ram_used, 16 * GB, 100 * ram_used / (16 * GB), 16 * GB - ram_used, cpu, procs, pressure,
                         60.0, 10)


def test_comparison_reports_only_meaningful_improvements():
    c = compare(ms(int(14.7 * GB), 24, 186, "HIGH"), ms(int(9.4 * GB), 8, 169, "NORMAL"))
    assert any("Recovered memory" in h for h in c.highlights)
    assert any("16 percentage points" in h for h in c.highlights)
    assert "Processes reduced: 17" in c.highlights

    noise = compare(ms(8 * GB, 10, 200, "NORMAL"), ms(8 * GB - 20 * MB, 9, 199, "NORMAL"))
    assert noise.highlights == []
    assert all(r.verdict == "no meaningful change" for r in noise.rows)
