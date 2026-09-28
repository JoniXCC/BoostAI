"""Google Gemini (optional cloud provider; free tier friendly - one request per explicit user action)."""

from __future__ import annotations

from boostai.ai.provider import AIError, AIProvider, http_json

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(AIProvider):
    name = "Google Gemini (cloud)"
    is_cloud = True

    def __init__(self, model: str, api_key: str, timeout_s: float = 60.0) -> None:
        super().__init__(model, timeout_s)
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise AIError("No Gemini API key configured.")
        return {"x-goog-api-key": self._api_key}  # header, never a URL parameter

    def complete_json(self, system: str, user: str) -> str:
        data = http_json(
            ENDPOINT.format(model=self.model),
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            },
            self._headers(),
            self.timeout_s,
        )
        try:
            return str(data["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("Unexpected response from Gemini.") from exc

    def health(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "No Gemini API key configured."
        return True, f"Gemini configured ({self.model}). A request is only made when you ask for AI advice."
