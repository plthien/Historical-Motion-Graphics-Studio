from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Event
from typing import Protocol, Sequence


logger = logging.getLogger(__name__)


class DurableWorkflowRunner(Protocol):
    """One domain-specific durable workflow runner registered with the shared host."""

    def run_once(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RegisteredDurableWorkflow:
    name: str
    runner: DurableWorkflowRunner


class DurableMediaWorkflowHost:
    """Fair process host for the registered durable media workflow domains.

    Domain runners retain their persistence, CAS, provider materialization, and
    error contracts. The host owns process-level scheduling and an optional
    runner cleanup lifecycle; domains without resources need not implement close.
    """

    def __init__(
        self,
        workflows: Sequence[RegisteredDurableWorkflow],
        *,
        host_logger: logging.Logger | None = None,
    ) -> None:
        if not workflows:
            raise ValueError("At least one durable workflow must be registered.")
        names = [item.name for item in workflows]
        if any(not name.strip() for name in names):
            raise ValueError("Durable workflow names must be non-empty.")
        if len(set(names)) != len(names):
            raise ValueError("Durable workflow names must be unique.")
        self.workflows = tuple(workflows)
        self.logger = host_logger or logger
        self._cursor = 0
        self._closed = False

    def run_once(self) -> bool:
        """Give every registered domain one bounded turn, rotating first priority."""
        if self._closed:
            return False
        progressed = False
        count = len(self.workflows)
        start = self._cursor % count
        self._cursor = (start + 1) % count
        for offset in range(count):
            item = self.workflows[(start + offset) % count]
            try:
                progressed = item.runner.run_once() or progressed
            except Exception:
                self.logger.exception("%s durable workflow iteration failed", item.name)
        return progressed

    def run_forever(
        self,
        stop: Event,
        *,
        idle_sleep_seconds: float = 0.5,
    ) -> None:
        while not stop.is_set():
            if not self.run_once():
                stop.wait(idle_sleep_seconds)

    def close(self) -> None:
        """Stop accepting turns and close resource-owning runners exactly once."""
        if self._closed:
            return
        self._closed = True
        for item in reversed(self.workflows):
            close = getattr(item.runner, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                self.logger.exception("%s durable workflow shutdown failed", item.name)
