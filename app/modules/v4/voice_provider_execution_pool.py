from __future__ import annotations

from concurrent.futures import Future, wait as wait_for_futures
from dataclasses import dataclass
from queue import Queue
from threading import Lock, Thread
from time import monotonic
from typing import Callable, Generic, TypeVar
from uuid import uuid4

T = TypeVar("T")
_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class VoiceProviderReservation:
    token: str


@dataclass(frozen=True, slots=True)
class VoiceProviderShutdownReport:
    completed_count: int
    cancelled_count: int
    running_count: int
    timed_out: bool


class VoiceProviderExecutionPool(Generic[T]):
    """Strictly bounded, non-authoritative executor for provider I/O.

    Capacity is reserved before durable task state may move to ``submitting``.
    The explicit outstanding budget covers reserved and dispatched actions, so
    no unbounded in-memory provider queue can form.

    Workers are daemon threads on purpose. Python cannot safely terminate a
    non-cooperative provider thread, so shutdown first cancels work that has not
    started, gives running actions a bounded grace period, then returns without
    allowing a stuck adapter to keep the workflow-host process alive forever.
    Durable correctness does not depend on a worker/Future surviving shutdown.
    """

    def __init__(self, *, max_workers: int = 1, max_outstanding: int | None = None) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        resolved_outstanding = max_workers if max_outstanding is None else int(max_outstanding)
        if resolved_outstanding <= 0:
            raise ValueError("max_outstanding must be positive")
        if resolved_outstanding < max_workers:
            raise ValueError("max_outstanding must be greater than or equal to max_workers")
        self._max_outstanding = resolved_outstanding
        self._futures: dict[str, Future[T]] = {}
        self._reservations: set[str] = set()
        self._lock = Lock()
        self._closed = False
        self._queue: Queue[object] = Queue()
        self._workers = tuple(
            Thread(target=self._worker_loop, name=f"voice-provider-{index + 1}", daemon=True)
            for index in range(max_workers)
        )
        for worker in self._workers:
            worker.start()

    @property
    def max_outstanding(self) -> int:
        return self._max_outstanding

    @property
    def outstanding_count(self) -> int:
        with self._lock:
            return len(self._reservations) + len(self._futures)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def try_reserve(self) -> VoiceProviderReservation | None:
        """Reserve one provider action slot without touching durable task state."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Voice provider execution pool is closed.")
            if len(self._reservations) + len(self._futures) >= self._max_outstanding:
                return None
            token = uuid4().hex
            self._reservations.add(token)
            return VoiceProviderReservation(token)

    def release_reservation(self, reservation: VoiceProviderReservation) -> None:
        with self._lock:
            self._reservations.discard(reservation.token)

    def dispatch_reserved(
        self,
        key: str,
        reservation: VoiceProviderReservation,
        action: Callable[[], T],
    ) -> bool:
        """Consume a reservation and enqueue exactly one bounded provider action."""
        with self._lock:
            if self._closed:
                self._reservations.discard(reservation.token)
                raise RuntimeError("Voice provider execution pool is closed.")
            if reservation.token not in self._reservations:
                return False
            if key in self._futures:
                self._reservations.discard(reservation.token)
                return False
            self._reservations.remove(reservation.token)
            future: Future[T] = Future()
            self._futures[key] = future
            self._queue.put((future, action))
            return True

    def dispatch(self, key: str, action: Callable[[], T]) -> bool:
        reservation = self.try_reserve()
        if reservation is None:
            return False
        return self.dispatch_reserved(key, reservation, action)

    def is_active(self, key: str) -> bool:
        with self._lock:
            future = self._futures.get(key)
            return future is not None and not future.done()

    def take_completed(self, key: str) -> T | None:
        with self._lock:
            future = self._futures.get(key)
            if future is None or not future.done():
                return None
            del self._futures[key]
        return future.result()

    def wait_until_settled(self, key: str, *, timeout_seconds: float) -> bool:
        with self._lock:
            future = self._futures.get(key)
        if future is None:
            return True
        try:
            future.result(timeout=max(0.0, float(timeout_seconds)))
        except TimeoutError:
            return False
        except BaseException:
            return True
        return True

    def forget(self, key: str) -> None:
        with self._lock:
            self._futures.pop(key, None)

    def drain_completed(self) -> list[T]:
        """Remove and return every successful completed result still owned by the pool."""
        with self._lock:
            completed = [future for future in self._futures.values() if future.done()]
            for key in [key for key, future in self._futures.items() if future.done()]:
                del self._futures[key]
        values: list[T] = []
        for future in completed:
            try:
                values.append(future.result())
            except Exception:
                continue
        return values

    def close(self, *, wait: bool = True, grace_seconds: float = 2.0) -> VoiceProviderShutdownReport:
        """Stop accepting work and return after a bounded cooperative grace period.

        ``wait`` is retained for existing callers but never means an unbounded
        join: ``wait=False`` uses zero grace; ``wait=True`` waits at most
        ``grace_seconds``. Running Python code cannot be killed safely, therefore
        daemon workers may finish after this method returns.
        """
        grace = max(0.0, float(grace_seconds)) if wait else 0.0
        with self._lock:
            first_close = not self._closed
            self._closed = True
            self._reservations.clear()
            futures = list(self._futures.values())
        cancelled_count = sum(1 for future in futures if future.cancel())
        done, not_done = wait_for_futures(futures, timeout=grace)
        if first_close:
            for _ in self._workers:
                self._queue.put(_SENTINEL)
        deadline = monotonic() + grace
        for worker in self._workers:
            remaining = max(0.0, deadline - monotonic())
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
        return VoiceProviderShutdownReport(
            completed_count=sum(1 for future in done if not future.cancelled()),
            cancelled_count=cancelled_count,
            running_count=sum(1 for future in not_done if future.running()),
            timed_out=bool(not_done),
        )

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                future, action = item  # type: ignore[misc]
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(action())
                except BaseException as exc:  # Future must retain provider failures for fenced harvest.
                    future.set_exception(exc)
            finally:
                self._queue.task_done()
