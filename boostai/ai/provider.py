"""AI provider abstraction.

Providers only turn a prompt into *text*. They have no access to the machine, and their
output is parsed and validated by :mod:`boostai.ai.advisor` before anything is shown.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any


class AIError(Exception):
    """Provider unavailable, quota exhausted, timeout or malformed transport response."""


class AIProvider(ABC):
    name: str = "provider"
    is_cloud: bool = False

    def __init__(self, model: str, timeout_s: float = 60.0) -> None:
        self.model = model
        self.timeout_s = timeout_s

    @abstractmethod
    def complete_json(self, system: str, user: str) -> str:
        """Return the model's raw text, which *should* be a JSON object."""

    @abstractmethod
    def health(self) -> tuple[bool, str]:
        """Cheap availability check for the Settings page."""


def http_json(url: str, payload: dict[str, Any] | None, headers: dict[str, str], timeout: float,
              method: str = "POST") -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https/localhost endpoints
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code == 429:
            raise AIError("Rate limit or free-tier quota reached. BoostAI continues with rule-based advice.") from exc
        if exc.code in (401, 403):
            raise AIError("The API key was rejected. Check it in Settings.") from exc
        raise AIError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise AIError(f"Could not reach the AI provider: {getattr(exc, 'reason', exc)}") from exc
    except ValueError as exc:
        raise AIError("The AI provider returned a non-JSON response.") from exc
