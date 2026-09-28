"""API-key storage: environment / .env first, then Windows Credential Manager.

Keys are never written to settings.json, the database or logs.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

ENV_VARS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}
TARGET = "BoostAI/{provider}"


def get_api_key(provider: str) -> str:
    env = os.environ.get(ENV_VARS.get(provider, ""), "").strip()
    if env:
        return env
    try:
        import win32cred

        cred = win32cred.CredRead(TARGET.format(provider=provider), win32cred.CRED_TYPE_GENERIC)
        blob = cred.get("CredentialBlob") or b""
        return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
    except Exception:
        return ""


def set_api_key(provider: str, key: str) -> bool:
    try:
        import win32cred

        target = TARGET.format(provider=provider)
        if not key:
            try:
                win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC)
            except Exception:
                pass
            return True
        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": provider,
            "CredentialBlob": key,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": "BoostAI API key",
        }, 0)
        return True
    except Exception as exc:
        log.warning("Could not store API key in Credential Manager: %s", type(exc).__name__)
        return False


def key_source(provider: str) -> str:
    if os.environ.get(ENV_VARS.get(provider, ""), "").strip():
        return "environment / .env"
    return "Windows Credential Manager" if get_api_key(provider) else "not set"
