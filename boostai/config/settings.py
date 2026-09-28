"""User settings (persisted as JSON in the data directory).

Secrets are intentionally *not* part of this model: API keys live in environment
variables / ``.env`` or in Windows Credential Manager (see ``boostai.ai.credentials``).
"""

from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from boostai.config import paths

log = logging.getLogger(__name__)


class Theme(str, Enum):
    DARK = "dark"
    LIGHT = "light"


class OptimizationMode(str, Enum):
    SAFE = "safe"          # only LOW-risk actions are executable; everything else is advice
    BALANCED = "balanced"  # LOW and MEDIUM risk actions (always after approval)


class AIProviderName(str, Enum):
    NONE = "none"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    GROQ = "groq"

    @property
    def is_cloud(self) -> bool:
        return self in (AIProviderName.GEMINI, AIProviderName.GROQ)


class MonitorSettings(BaseModel):
    system_interval_s: float = Field(2.0, ge=1.0, le=60.0)
    process_interval_s: float = Field(10.0, ge=3.0, le=300.0)
    background_interval_s: float = Field(30.0, ge=5.0, le=600.0)
    persist_interval_s: float = Field(60.0, ge=15.0, le=900.0)
    history_minutes: int = Field(120, ge=15, le=720)
    top_processes_persisted: int = Field(25, ge=5, le=100)


class DetectionSettings(BaseModel):
    leak_min_minutes: float = Field(10.0, ge=2.0)
    leak_min_growth_mb: float = Field(200.0, ge=10.0)
    leak_min_relative_growth: float = Field(0.30, ge=0.05)
    cpu_high_percent: float = Field(85.0, ge=30.0, le=100.0)
    cpu_sustained_minutes: float = Field(3.0, ge=0.5)
    process_cpu_monopoly_percent: float = Field(50.0, ge=10.0, le=100.0)
    idle_cpu_percent: float = Field(15.0, ge=2.0, le=80.0)
    user_idle_seconds: float = Field(120.0, ge=30.0)
    disk_free_warn_percent: float = Field(10.0, ge=1.0, le=50.0)
    disk_free_warn_gb: float = Field(15.0, ge=1.0)
    ram_high_percent: float = Field(85.0, ge=50.0, le=99.0)
    gpu_temp_warn_c: float = Field(87.0, ge=60.0, le=110.0)
    process_count_warn: int = Field(320, ge=100)


class AISettings(BaseModel):
    provider: AIProviderName = AIProviderName.NONE
    local_only: bool = True  # hard block for cloud providers
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    gemini_model: str = "gemini-2.5-flash"
    groq_model: str = "llama-3.3-70b-versatile"
    timeout_s: float = Field(90.0, ge=5.0, le=600.0)
    auto_explain_after_scan: bool = False


class RetentionSettings(BaseModel):
    raw_system_samples_hours: int = Field(24, ge=1, le=168)
    compacted_system_samples_days: int = Field(30, ge=1, le=365)
    process_samples_days: int = Field(7, ge=1, le=90)
    scans_days: int = Field(180, ge=7, le=3650)


class Settings(BaseModel):
    theme: Theme = Theme.DARK
    mode: OptimizationMode = OptimizationMode.BALANCED
    monitor: MonitorSettings = MonitorSettings()
    detection: DetectionSettings = DetectionSettings()
    ai: AISettings = AISettings()
    retention: RetentionSettings = RetentionSettings()
    minimize_to_tray: bool = True
    start_with_windows: bool = False
    settle_seconds: float = Field(15.0, ge=3.0, le=120.0)
    offer_restore_point: bool = True
    tray_notifications: bool = True
    cleanup_min_age_hours: float = Field(24.0, ge=1.0, le=720.0)


class SettingsStore:
    """Loads and saves :class:`Settings`, tolerating missing/corrupt files."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.settings_path()
        self._lock = threading.Lock()
        self.settings = self.load()

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            return Settings.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("Settings file unreadable, using defaults: %s", exc)
            return Settings()

    def save(self, settings: Settings | None = None) -> None:
        with self._lock:
            if settings is not None:
                self.settings = settings
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self.settings.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(self.path)
