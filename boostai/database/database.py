"""SQLite connection management and schema.

A single connection (WAL mode) guarded by a re-entrant lock is shared across threads.
Write volume is low by design: the monitor batches samples and persists roughly once a
minute, and old data is compacted/expired by :meth:`Repository.run_maintenance`.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        REAL NOT NULL,
    kind             TEXT NOT NULL,
    cpu_usage        REAL,
    memory_usage     REAL,
    memory_used      INTEGER,
    memory_total     INTEGER,
    available_memory INTEGER,
    commit_percent   REAL,
    memory_pressure  TEXT,
    disk_usage       REAL,
    process_count    INTEGER,
    health_score     REAL,
    scores_json      TEXT,
    issue_count      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scans_ts ON scans(timestamp);

CREATE TABLE IF NOT EXISTS system_samples (
    timestamp      REAL NOT NULL,
    cpu            REAL,
    ram_percent    REAL,
    available      INTEGER,
    commit_percent REAL,
    disk_read_bps  REAL,
    disk_write_bps REAL,
    process_count  INTEGER,
    user_idle      REAL,
    compacted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sys_ts ON system_samples(timestamp);

CREATE TABLE IF NOT EXISTS process_samples (
    timestamp    REAL NOT NULL,
    process_name TEXT NOT NULL,
    pid          INTEGER NOT NULL,
    create_time  REAL NOT NULL,
    memory_bytes INTEGER NOT NULL,
    working_set  INTEGER,
    cpu_percent  REAL,
    threads      INTEGER,
    handles      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_proc_ts ON process_samples(timestamp);
CREATE INDEX IF NOT EXISTS idx_proc_name_ts ON process_samples(process_name, timestamp);

CREATE TABLE IF NOT EXISTS issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     INTEGER REFERENCES scans(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    severity    TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    root_cause  TEXT,
    component   TEXT,
    evidence    TEXT,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    detected_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_scan ON issues(scan_id);

CREATE TABLE IF NOT EXISTS actions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id          TEXT NOT NULL,
    timestamp          REAL NOT NULL,
    target             TEXT,
    params_json        TEXT,
    previous_state     TEXT,
    new_state          TEXT,
    result             TEXT NOT NULL,
    verification       TEXT,
    message            TEXT,
    reversibility      TEXT,
    rollback_available INTEGER NOT NULL DEFAULT 0,
    rolled_back        INTEGER NOT NULL DEFAULT 0,
    rollback_timestamp REAL,
    rollback_result    TEXT,
    session_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(timestamp);

CREATE TABLE IF NOT EXISTS baselines (
    process_name TEXT NOT NULL,
    metric       TEXT NOT NULL,
    median       REAL,
    p95          REAL,
    mean         REAL,
    samples      INTEGER,
    updated_at   REAL,
    PRIMARY KEY (process_name, metric)
);

CREATE TABLE IF NOT EXISTS comparisons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL NOT NULL,
    before_json TEXT NOT NULL,
    after_json  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    action_ids  TEXT
);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA optimize")
            except sqlite3.Error:
                pass
            self._conn.close()
