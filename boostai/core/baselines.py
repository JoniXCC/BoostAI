"""Per-PC baselines ("what is normal on *this* machine").

Baselines are recomputed from persisted telemetry (default: last 7 days). Only the
top-N memory consumers are persisted each minute, so per-process baselines exist for
the applications that matter for performance. A baseline is only trusted once it has
at least ``MIN_SAMPLES`` observations; until then it is reported as "learning".
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from boostai.core.trend import mean, percentile
from boostai.database.repository import BaselineRow, Repository

MIN_SAMPLES = 30
SYSTEM = "__system__"


@dataclass(slots=True)
class Baseline:
    median: float
    p95: float
    mean: float
    samples: int

    @property
    def reliable(self) -> bool:
        return self.samples >= MIN_SAMPLES


def summarize(values: list[float]) -> Baseline | None:
    if not values:
        return None
    return Baseline(median=percentile(values, 50), p95=percentile(values, 95), mean=mean(values), samples=len(values))


class BaselineEngine:
    def __init__(self, repo: Repository | None) -> None:
        self.repo = repo
        self._cache: dict[tuple[str, str], Baseline] = {}
        self.updated_at: float | None = None
        if repo is not None:
            self.load()

    def load(self) -> None:
        self._cache = {
            (r.process_name, r.metric): Baseline(r.median, r.p95, r.mean, r.samples) for r in self.repo.all_baselines()
        }

    def refresh(self, days: int = 7, now: float | None = None) -> int:
        """Recompute all baselines from telemetry. Returns the number of baselines written."""
        if self.repo is None:
            return 0
        now = now or time.time()
        since = now - days * 86400
        mem: dict[str, list[float]] = defaultdict(list)
        cpu: dict[str, list[float]] = defaultdict(list)
        for name, _ts, mem_sum, cpu_sum in self.repo.process_name_totals_since(since):
            mem[name].append(float(mem_sum))
            cpu[name].append(float(cpu_sum or 0.0))

        rows: list[BaselineRow] = []
        for name in mem:
            for metric, values in (("memory", mem[name]), ("cpu", cpu[name])):
                b = summarize(values)
                if b:
                    rows.append(BaselineRow(name, metric, b.median, b.p95, b.mean, b.samples, now))

        system = self.repo.system_samples_since(since)
        idle_cpu = [s["cpu"] for s in system if (s.get("user_idle") or 0) >= 120 and s["cpu"] is not None]
        for metric, values in (
            ("ram_percent", [s["ram_percent"] for s in system if s["ram_percent"] is not None]),
            ("process_count", [float(s["process_count"]) for s in system if s["process_count"]]),
            ("idle_cpu", idle_cpu),
            ("cpu", [s["cpu"] for s in system if s["cpu"] is not None]),
        ):
            b = summarize(values)
            if b:
                rows.append(BaselineRow(SYSTEM, metric, b.median, b.p95, b.mean, b.samples, now))

        if rows:
            self.repo.upsert_baselines(rows)
        self.load()
        self.updated_at = now
        return len(rows)

    def process(self, name: str, metric: str = "memory") -> Baseline | None:
        return self._cache.get((name.lower(), metric))

    def system(self, metric: str) -> Baseline | None:
        return self._cache.get((SYSTEM, metric))

    def set(self, name: str, metric: str, baseline: Baseline) -> None:
        """Inject a baseline (used by tests and the demo data generator)."""
        self._cache[(name.lower(), metric)] = baseline

    def count(self) -> int:
        return len(self._cache)
