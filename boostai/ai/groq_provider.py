"""Groq (optional cloud provider, OpenAI-compatible API)."""

from __future__ import annotations

from boostai.ai.provider import AIError, AIProvider, http_json

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(AIProvider):
    name = "Groq (cloud)"
    is_cloud = True

    def __init__(self, model: str, api_key: str, timeout_s: float = 60.0) -> None:
        super().__init__(model, timeout_s)
        self._api_key = api_key

    def complete_json(self, system: str, user: str) -> str:
        if not self._api_key:
            raise AIError("No Groq API key configured.")
        data = http_json(
            ENDPOINT,
            {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            {"Authorization": f"Bearer {self._api_key}"},
            self.timeout_s,
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("Unexpected response from Groq.") from exc

    def health(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "No Groq API key configured."
        return True, f"Groq configured ({self.model}). A request is only made when you ask for AI advice."
