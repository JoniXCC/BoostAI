"""Typed data-access layer. All SQL lives here."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

from boostai.core.issues import Issue
from boostai.database.database import Database

COMPACT_BUCKET_SECONDS = 300


@dataclass(slots=True)
class SystemSampleRow:
    timestamp: float
    cpu: float
    ram_percent: float
    available: int
    commit_percent: float | None
    disk_read_bps: float
    disk_write_bps: float
    process_count: int
    user_idle: float | None


@dataclass(slots=True)
class ProcessSampleRow:
    timestamp: float
    process_name: str
    pid: int
    create_time: float
    memory_bytes: int
    working_set: int
    cpu_percent: float
    threads: int
    handles: int | None


@dataclass(slots=True)
class BaselineRow:
    process_name: str
    metric: str
    median: float
    p95: float
    mean: float
    samples: int
    updated_at: float


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ scans
    def insert_scan(self, record: dict[str, Any]) -> int:
        cols = [
            "timestamp", "kind", "cpu_usage", "memory_usage", "memory_used", "memory_total",
            "available_memory", "commit_percent", "memory_pressure", "disk_usage", "process_count",
            "health_score", "scores_json", "issue_count",
        ]
        values = [record.get(c) for c in cols]
        with self.db.transaction() as conn:
            cur = conn.execute(
                f"INSERT INTO scans({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values
            )
            return int(cur.lastrowid)

    def recent_scans(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM scans ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- issues
    def insert_issues(self, scan_id: int, issues: Iterable[Issue]) -> None:
        with self.db.transaction() as conn:
            for issue in issues:
                cur = conn.execute(
                    "INSERT INTO issues(scan_id, key, type, title, severity, confidence, root_cause, component,"
                    " evidence, payload, status, detected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        scan_id, issue.key, issue.type.value, issue.title, issue.severity.value,
                        issue.confidence.value, issue.root_cause.value, issue.component,
                        _dumps(issue.evidence), issue.model_dump_json(), issue.status, issue.detected_at,
                    ),
                )
                issue.db_id = int(cur.lastrowid)

    def issues_for_scan(self, scan_id: int) -> list[Issue]:
        rows = self.db.query("SELECT id, payload, status FROM issues WHERE scan_id=? ORDER BY id", (scan_id,))
        out = []
        for r in rows:
            issue = Issue.model_validate_json(r["payload"])
            issue.db_id, issue.status = r["id"], r["status"]
            out.append(issue)
        return out

    def update_issue_status(self, issue_id: int, status: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE issues SET status=? WHERE id=?", (status, issue_id))

    # ---------------------------------------------------------------- samples
    def insert_system_samples(self, rows: list[SystemSampleRow]) -> None:
        if not rows:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO system_samples(timestamp, cpu, ram_percent, available, commit_percent,"
                " disk_read_bps, disk_write_bps, process_count, user_idle) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (r.timestamp, r.cpu, r.ram_percent, r.available, r.commit_percent, r.disk_read_bps,
                     r.disk_write_bps, r.process_count, r.user_idle)
                    for r in rows
                ],
            )

    def insert_process_samples(self, rows: list[ProcessSampleRow]) -> None:
        if not rows:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO process_samples(timestamp, process_name, pid, create_time, memory_bytes,"
                " working_set, cpu_percent, threads, handles) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (r.timestamp, r.process_name, r.pid, r.create_time, r.memory_bytes, r.working_set,
                     r.cpu_percent, r.threads, r.handles)
                    for r in rows
                ],
            )

    def system_samples_since(self, since: float) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM system_samples WHERE timestamp>=? ORDER BY timestamp", (since,))
        return [dict(r) for r in rows]

    def process_samples_since(self, since: float) -> list[ProcessSampleRow]:
        rows = self.db.query(
            "SELECT timestamp, process_name, pid, create_time, memory_bytes, working_set, cpu_percent, threads,"
            " handles FROM process_samples WHERE timestamp>=? ORDER BY timestamp",
            (since,),
        )
        return [ProcessSampleRow(*tuple(r)) for r in rows]

    def process_name_totals_since(self, since: float) -> list[tuple[str, float, float, float]]:
        """(name, timestamp, summed private bytes, summed cpu%) per sampling instant."""
        rows = self.db.query(
            "SELECT lower(process_name), timestamp, SUM(memory_bytes), SUM(cpu_percent) FROM process_samples"
            " WHERE timestamp>=? GROUP BY lower(process_name), timestamp",
            (since,),
        )
        return [tuple(r) for r in rows]

    # -------------------------------------------------------------- baselines
    def upsert_baselines(self, rows: list[BaselineRow]) -> None:
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO baselines(process_name, metric, median, p95, mean, samples, updated_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(process_name, metric) DO UPDATE SET"
                " median=excluded.median, p95=excluded.p95, mean=excluded.mean, samples=excluded.samples,"
                " updated_at=excluded.updated_at",
                [(r.process_name, r.metric, r.median, r.p95, r.mean, r.samples, r.updated_at) for r in rows],
            )

    def all_baselines(self) -> list[BaselineRow]:
        rows = self.db.query("SELECT process_name, metric, median, p95, mean, samples, updated_at FROM baselines")
        return [BaselineRow(*tuple(r)) for r in rows]

    # ---------------------------------------------------------------- actions
    def insert_action(self, record: dict[str, Any]) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO actions(action_id, timestamp, target, params_json, previous_state, new_state, result,"
                " verification, message, reversibility, rollback_available, session_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record["action_id"], record.get("timestamp", time.time()), record.get("target"),
                    _dumps(record.get("params", {})), _dumps(record.get("previous_state")),
                    _dumps(record.get("new_state")), record["result"], _dumps(record.get("verification")),
                    record.get("message"), record.get("reversibility"), int(bool(record.get("rollback_available"))),
                    record.get("session_id"),
                ),
            )
            return int(cur.lastrowid)

    def get_action(self, record_id: int) -> dict[str, Any] | None:
        rows = self.db.query("SELECT * FROM actions WHERE id=?", (record_id,))
        return _decode_action(rows[0]) if rows else None

    def recent_actions(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM actions ORDER BY timestamp DESC, id DESC LIMIT ?", (limit,))
        return [_decode_action(r) for r in rows]

    def actions_for_session(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM actions WHERE session_id=? ORDER BY id", (session_id,))
        return [_decode_action(r) for r in rows]

    def mark_rolled_back(self, record_id: int, result: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE actions SET rolled_back=?, rollback_timestamp=?, rollback_result=? WHERE id=?",
                (1 if result == "SUCCESS" else 0, time.time(), result, record_id),
            )

    # ------------------------------------------------------------ comparisons
    def insert_comparison(self, before: dict, after: dict, summary: dict, action_ids: list[int]) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO comparisons(timestamp, before_json, after_json, summary, action_ids) VALUES (?,?,?,?,?)",
                (time.time(), _dumps(before), _dumps(after), _dumps(summary), _dumps(action_ids)),
            )
            return int(cur.lastrowid)

    def recent_comparisons(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM comparisons ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [
            {
                "id": r["id"], "timestamp": r["timestamp"], "before": json.loads(r["before_json"]),
                "after": json.loads(r["after_json"]), "summary": json.loads(r["summary"]),
                "action_ids": json.loads(r["action_ids"] or "[]"),
            }
            for r in rows
        ]

    # ------------------------------------------------------------ maintenance
    def run_maintenance(
        self, raw_hours: int, compacted_days: int, process_days: int, scan_days: int, now: float | None = None
    ) -> dict[str, int]:
        """Compact raw system samples into 5-minute averages and expire old data."""
        now = now or time.time()
        raw_cutoff = now - raw_hours * 3600
        stats: dict[str, int] = {}
        with self.db.transaction() as conn:
            buckets = conn.execute(
                "SELECT CAST(timestamp / ? AS INTEGER) * ? AS bucket, AVG(cpu), AVG(ram_percent), AVG(available),"
                " AVG(commit_percent), AVG(disk_read_bps), AVG(disk_write_bps), AVG(process_count), AVG(user_idle)"
                " FROM system_samples WHERE compacted=0 AND timestamp < ? GROUP BY bucket",
                (COMPACT_BUCKET_SECONDS, COMPACT_BUCKET_SECONDS, raw_cutoff),
            ).fetchall()
            deleted = conn.execute(
                "DELETE FROM system_samples WHERE compacted=0 AND timestamp < ?", (raw_cutoff,)
            ).rowcount
            conn.executemany(
                "INSERT INTO system_samples(timestamp, cpu, ram_percent, available, commit_percent, disk_read_bps,"
                " disk_write_bps, process_count, user_idle, compacted) VALUES (?,?,?,?,?,?,?,?,?,1)",
                [tuple(b) for b in buckets],
            )
            stats["system_compacted_from"] = deleted
            stats["system_compacted_to"] = len(buckets)
            stats["system_expired"] = conn.execute(
                "DELETE FROM system_samples WHERE timestamp < ?", (now - compacted_days * 86400,)
            ).rowcount
            stats["process_expired"] = conn.execute(
                "DELETE FROM process_samples WHERE timestamp < ?", (now - process_days * 86400,)
            ).rowcount
            stats["scans_expired"] = conn.execute(
                "DELETE FROM scans WHERE timestamp < ?", (now - scan_days * 86400,)
            ).rowcount
            conn.execute("DELETE FROM comparisons WHERE timestamp < ?", (now - scan_days * 86400,))
        return stats

    def table_counts(self) -> dict[str, int]:
        tables = ["scans", "system_samples", "process_samples", "issues", "actions", "baselines", "comparisons"]
        return {t: int(self.db.query(f"SELECT COUNT(*) FROM {t}")[0][0]) for t in tables}


def _decode_action(row) -> dict[str, Any]:
    d = dict(row)
    for key in ("params_json", "previous_state", "new_state", "verification"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except ValueError:
                pass
    d["params"] = d.pop("params_json", {}) or {}
    return d
