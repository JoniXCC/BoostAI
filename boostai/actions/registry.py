"""The action whitelist. If an action ID is not registered here, it does not exist."""

from __future__ import annotations

from boostai.actions.action_models import ActionID
from boostai.actions.handlers.base import ActionDefinition, ActionHandler
from boostai.actions.handlers.cleanup import ClearCacheHandler, ClearTempHandler
from boostai.actions.handlers.process import CloseProcessHandler, RestartProcessHandler, TrimWorkingSetHandler
from boostai.actions.handlers.service import RestartServiceHandler, StartServiceHandler, StopServiceHandler
from boostai.actions.handlers.startup import DisableStartupHandler, EnableStartupHandler
from boostai.actions.handlers.system import FlushDnsHandler, PowerPlanHandler, RestorePointHandler


def default_handlers() -> list[ActionHandler]:
    return [
        RestartProcessHandler(), CloseProcessHandler(), TrimWorkingSetHandler(),
        DisableStartupHandler(), EnableStartupHandler(),
        ClearTempHandler(), ClearCacheHandler(),
        PowerPlanHandler(), FlushDnsHandler(),
        StopServiceHandler(), StartServiceHandler(), RestartServiceHandler(),
        RestorePointHandler(),
    ]


class ActionRegistry:
    def __init__(self, handlers: list[ActionHandler] | None = None) -> None:
        self._handlers: dict[str, ActionHandler] = {}
        for handler in handlers or default_handlers():
            action_id = handler.definition.action_id.value
            if action_id in self._handlers:
                raise ValueError(f"Duplicate handler for {action_id}")
            self._handlers[action_id] = handler
        if handlers is None:
            missing = {a.value for a in ActionID} - set(self._handlers)
            if missing:
                raise RuntimeError(f"Actions without handlers: {sorted(missing)}")

    def get(self, action_id: object) -> ActionHandler | None:
        """Exact, case-sensitive lookup. Anything that is not a known ID string returns None."""
        if not isinstance(action_id, str):
            return None
        return self._handlers.get(action_id)

    def is_known(self, action_id: object) -> bool:
        return self.get(action_id) is not None

    def ids(self) -> list[str]:
        return list(self._handlers)

    def definition(self, action_id: str) -> ActionDefinition | None:
        handler = self.get(action_id)
        return handler.definition if handler else None

    def definitions(self) -> list[ActionDefinition]:
        return [h.definition for h in self._handlers.values()]
