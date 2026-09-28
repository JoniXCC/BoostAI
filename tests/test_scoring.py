import pytest

from boostai.core.performance_score import ScoreInputs, compute_score, label_for, piecewise


def test_piecewise_interpolation_and_clamping():
    pts = [(0, 100), (50, 50), (100, 0)]
    assert piecewise(-5, pts) == 100
    assert piecewise(25, pts) == 75
    assert piecewise(150, pts) == 0


def test_healthy_machine_scores_high():
    s = compute_score(ScoreInputs(ram_percent=40, commit_percent=50, cpu_avg_percent=5, system_disk_free_percent=60,
                                  system_disk_free_gb=300, disk_busy_percent=5, disk_latency_ms=1, process_count=200))
    assert s.overall >= 95 and s.label == "Good"


def test_memory_pressure_and_leaks_lower_memory_category():
    base = ScoreInputs(ram_percent=94, commit_percent=93, hard_faults_per_sec=3500, possible_leaks=1)
    s = compute_score(base)
    assert s.categories["Memory"].score < 20
    assert any("leak" in d for d in s.categories["Memory"].details)


def test_missing_categories_are_excluded_not_zeroed():
    s = compute_score(ScoreInputs(ram_percent=40, cpu_avg_percent=5, startup_known=False, process_count=150))
    assert "Responsiveness" not in s.categories and "Disk capacity" not in s.categories
    assert "Startup" not in s.categories
    assert s.overall == pytest.approx(100)


def test_low_disk_space_capped():
    s = compute_score(ScoreInputs(ram_percent=40, system_disk_free_percent=30, system_disk_free_gb=8))
    assert s.categories["Disk capacity"].score == 40


def test_startup_penalties():
    s = compute_score(ScoreInputs(ram_percent=40, startup_high_impact=3, startup_optional=5, startup_unknown=2))
    assert s.categories["Startup"].score == 100 - 30 - 20 - 4


def test_process_baseline_ratio_used_when_available():
    s = compute_score(ScoreInputs(ram_percent=40, process_count=300, process_count_baseline=150))
    assert s.categories["Background load"].score == pytest.approx(30)


def test_labels():
    assert label_for(90) == "Good" and label_for(75) == "Fair"
    assert label_for(55) == "Needs attention" and label_for(10) == "Poor"


def test_weights_sum_to_one():
    from boostai.core.performance_score import WEIGHTS

    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
