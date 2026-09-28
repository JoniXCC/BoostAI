"""Privilege helpers. BoostAI runs as a standard user; elevation is per action."""

from __future__ import annotations

from boostai.utils import winapi


def is_admin() -> bool:
    return winapi.is_admin()


def elevation_explanation(action_name: str, reason: str) -> str:
    return (
        f"'{action_name}' needs administrator permission because {reason}.\n\n"
        "Windows will show a UAC prompt. If you accept, a short-lived BoostAI helper runs only this "
        "one predefined action (it re-validates it against the same whitelist) and then exits. "
        "The main BoostAI window keeps running without administrator rights."
    )
