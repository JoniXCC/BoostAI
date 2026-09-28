"""Local model via Ollama (default: nothing leaves the computer)."""

from __future__ import annotations

from urllib.parse import urlparse

from boostai.ai.provider import AIError, AIProvider, http_json

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OllamaProvider(AIProvider):
    name = "Ollama (local)"
    is_cloud = False

    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout_s: float = 90.0) -> None:
        super().__init__(model, timeout_s)
        self.base_url = base_url.rstrip("/")
        host = urlparse(self.base_url).hostname or ""
        # A non-local Ollama endpoint would send data off this machine: treat it as cloud.
        self.is_cloud = host not in LOCAL_HOSTS

    def complete_json(self, system: str, user: str) -> str:
        data = http_json(
            f"{self.base_url}/api/chat",
            {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.2},
                "keep_alive": "2m",  # unload soon after use: no continuously resident LLM
            },
            {},
            self.timeout_s,
        )
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise AIError("Unexpected response from Ollama.") from exc

    def health(self) -> tuple[bool, str]:
        try:
            data = http_json(f"{self.base_url}/api/tags", None, {}, 5.0, method="GET")
        except AIError as exc:
            return False, f"Ollama not reachable at {self.base_url} ({exc}). Start Ollama or choose another provider."
        models = [m.get("name", "") for m in data.get("models", [])]
        if self.model not in models and f"{self.model}:latest" not in models:
            listed = ", ".join(models) or "none"
            return False, f"Model '{self.model}' is not installed (installed: {listed}). Run: ollama pull {self.model}"
        return True, f"Ollama is running with {self.model}."
