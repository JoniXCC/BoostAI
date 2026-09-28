"""Structured, size-bounded, secret-redacting logging.

Every audit-relevant event (scan_started, action_executed, rollback_completed, ...)
is written through :func:`log_event` as one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from boostai.config import paths

AUDIT_LOGGER = "boostai.audit"

EVENTS = frozenset(
    {
        "app_started",
        "app_stopped",
        "scan_started",
        "scan_finished",
        "scan_failed",
        "issue_detected",
        "action_recommended",
        "action_approved",
        "action_rejected",
        "action_blocked",
        "action_executed",
        "action_failed",
        "verification_completed",
        "rollback_started",
        "rollback_completed",
        "rollback_failed",
        "elevation_requested",
        "ai_request",
        "ai_response_rejected",
        "gaming_mode_started",
        "gaming_mode_ended",
        "deep_scan_started",
        "deep_scan_finished",
    }
)

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|password|secret|authorization|credential)", re.I)
_SECRET_VALUE_RES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),   # Google API keys
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),       # Groq keys
    re.compile(r"sk-[0-9A-Za-z_\-]{20,}"),     # generic OpenAI-style keys
    re.compile(r"(?i)bearer\s+[0-9A-Za-z._\-]{10,}"),
    re.compile(r"(?i)([?&]key=)[^&\s]+"),
]


def redact(value: Any) -> Any:
    """Recursively remove secrets from log payloads."""
    if isinstance(value, dict):
        return {
            k: ("***" if _SECRET_KEY_RE.search(str(k)) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for pattern in _SECRET_VALUE_RES:
            value = pattern.sub("***", value)
        return value
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
            payload.update(getattr(record, "fields", {}))
        else:
            payload["msg"] = record.getMessage()
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str, ensure_ascii=False)


_configured = False


def configure_logging(level: int = logging.INFO, to_console: bool = False) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("boostai")
    root.setLevel(level)
    handler = RotatingFileHandler(
        paths.log_dir() / "boostai.jsonl", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        console.addFilter(RedactingFilter())
        root.addHandler(console)
    _configured = True


def log_event(event: str, **fields: Any) -> None:
    """Write a structured audit event."""
    if event not in EVENTS:
        raise ValueError(f"Unknown audit event: {event}")
    logging.getLogger(AUDIT_LOGGER).info(event, extra={"event": event, "fields": redact(fields)})
