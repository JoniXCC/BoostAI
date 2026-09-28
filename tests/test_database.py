import time

from boostai.core.baselines import BaselineEngine
from boostai.core.issues import Confidence, Issue, IssueType, RootCause, Severity
from boostai.database.repository import ProcessSampleRow, SystemSampleRow


def test_scan_and_issue_roundtrip(repo):
    scan_id = repo.insert_scan({"timestamp": time.time(), "kind": "quick", "cpu_usage": 12.5, "memory_usage": 80.0,
                                "process_count": 200, "health_score": 71.0, "issue_count": 1})
    issue = Issue(key="K", type=IssueType.MEMORY_PRESSURE, title="t", severity=Severity.HIGH,
                  confidence=Confidence.HIGH, root_cause=RootCause.WORKLOAD, component="RAM", explanation="e",
                  evidence=["a", "b"])
    repo.insert_issues(scan_id, [issue])
    loaded = repo.issues_for_scan(scan_id)
    assert loaded[0].title == "t" and loaded[0].evidence == ["a", "b"] and loaded[0].db_id
    repo.update_issue_status(loaded[0].db_id, "resolved")
    assert repo.issues_for_scan(scan_id)[0].status == "resolved"
    assert repo.recent_scans()[0]["health_score"] == 71.0


def test_action_record_roundtrip(repo):
    rid = repo.insert_action({"action_id": "FLUSH_DNS", "target": "dns", "params": {}, "result": "SUCCESS",
                              "previous_state": {"a": 1}, "verification": {"status": "SUCCESS"},
                              "rollback_available": False, "session_id": "s"})
    rec = repo.get_action(rid)
    assert rec["previous_state"] == {"a": 1} and rec["params"] == {} and rec["verification"]["status"] == "SUCCESS"
    repo.mark_rolled_back(rid, "SUCCESS")
    assert repo.get_action(rid)["rolled_back"] == 1
    assert repo.actions_for_session("s")[0]["id"] == rid


def test_maintenance_compacts_and_expires(repo):
    now = 10_000_000.0
    old = [SystemSampleRow(now - 2 * 86400 + i * 10, 10.0 + i, 50.0, 1, 50.0, 0, 0, 100, None) for i in range(60)]
    recent = [SystemSampleRow(now - 60, 5.0, 40.0, 1, 40.0, 0, 0, 90, None)]
    ancient = [SystemSampleRow(now - 90 * 86400, 1.0, 1.0, 1, 1.0, 0, 0, 1, None)]
    repo.insert_system_samples(old + recent + ancient)
    repo.insert_process_samples([ProcessSampleRow(now - 30 * 86400, "a.exe", 1, 1.0, 100, 100, 0.0, 1, 1),
                                 ProcessSampleRow(now - 10, "a.exe", 1, 1.0, 100, 100, 0.0, 1, 1)])
    stats = repo.run_maintenance(raw_hours=24, compacted_days=30, process_days=7, scan_days=180, now=now)
    assert stats["system_compacted_from"] == 61  # 60 old + the ancient one
    expected_buckets = len({int(r.timestamp // 300) for r in old}) + 1  # + the ancient sample's bucket
    assert stats["system_compacted_to"] == expected_buckets
    assert stats["system_expired"] == 1
    assert stats["process_expired"] == 1
    rows = repo.system_samples_since(0)
    assert len(rows) == expected_buckets and sum(r["compacted"] for r in rows) == expected_buckets - 1


def test_baseline_refresh_from_samples(repo):
    now = time.time()
    rows = [ProcessSampleRow(now - i * 60, "Discord.exe", 1, 1.0, (400 + i) * 1024**2, 0, 1.0, 1, 1) for i in range(40)]
    repo.insert_process_samples(rows)
    engine = BaselineEngine(repo)
    assert engine.refresh(now=now) > 0
    b = engine.process("discord.exe")
    assert b.reliable and 400 * 1024**2 <= b.median <= 440 * 1024**2
    assert BaselineEngine(repo).process("discord.exe") is not None  # persisted


def test_table_counts(repo):
    counts = repo.table_counts()
    assert set(counts) >= {"scans", "actions", "baselines", "process_samples"}
