import os
import tempfile

# Isolate every test run from the user's real BoostAI data before anything imports boostai.
os.environ["BOOSTAI_DATA_DIR"] = tempfile.mkdtemp(prefix="boostai-tests-")
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)

import pytest  # noqa: E402

from boostai.actions.executor import ActionExecutor  # noqa: E402
from boostai.actions.handlers.base import ActionContext  # noqa: E402
from boostai.actions.registry import ActionRegistry  # noqa: E402
from boostai.config.settings import OptimizationMode, Settings  # noqa: E402
from boostai.database.database import Database  # noqa: E402
from boostai.database.repository import Repository  # noqa: E402
from tests.fakes import USER, FakeSystemOps  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def ops() -> FakeSystemOps:
    return FakeSystemOps()


@pytest.fixture
def repo() -> Repository:
    return Repository(Database(":memory:"))


@pytest.fixture
def ctx(ops, settings) -> ActionContext:
    return ActionContext(ops=ops, settings=settings, self_pid=4242, user=USER, is_admin=False,
                         sleep=lambda _s: None)


@pytest.fixture
def registry() -> ActionRegistry:
    return ActionRegistry()


@pytest.fixture
def executor(registry, ctx, repo, settings):
    return ActionExecutor(registry, lambda: ctx, repo, broker=None, mode_provider=lambda: settings.mode)


@pytest.fixture(autouse=True)
def _forbid_shell(monkeypatch):
    """Belt and braces: no test may spawn a shell or a process."""
    import subprocess

    def _boom(*_a, **_k):
        raise AssertionError("Tests must never spawn processes or shells")

    monkeypatch.setattr(os, "system", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)


@pytest.fixture
def safe_mode(settings):
    settings.mode = OptimizationMode.SAFE
    return settings
