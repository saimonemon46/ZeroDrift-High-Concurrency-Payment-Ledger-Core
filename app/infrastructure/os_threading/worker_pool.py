"""Thread-safe bounded worker pool using condition variables and Linux signal draining.

Eliminates CPU spin-locking by placing idle worker threads in sleep state via
threading.Condition. Intercepts SIGTERM and SIGINT to cleanly drain active tasks
before process termination.
"""

import logging
import signal
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class ThreadSafeWorkerPool:
    """Bounded worker thread pool synchronized via condition variables."""

    def __init__(
        self,
        worker_count: int = 4,
        max_queue_size: int = 1000,
        enable_signal_handlers: bool = False,
    ) -> None:
        if worker_count <= 0:
            raise ValueError(f"Worker count must be > 0, got {worker_count}.")
        if max_queue_size <= 0:
            raise ValueError(f"Max queue size must be > 0, got {max_queue_size}.")

        self.worker_count: int = worker_count
        self.max_queue_size: int = max_queue_size

        self._queue: deque[Callable[[], Any]] = deque()
        self._lock: threading.Lock = threading.Lock()
        self._not_empty: threading.Condition = threading.Condition(self._lock)
        self._not_full: threading.Condition = threading.Condition(self._lock)

        self._is_running: bool = False
        self._is_shutdown: bool = False
        self._workers: list[threading.Thread] = []

        if enable_signal_handlers:
            self._register_signal_handlers()

    def _register_signal_handlers(self) -> None:
        """Hooks into OS termination signals to trigger graceful drain."""
        try:
            signal.signal(signal.SIGTERM, self._handle_os_signal)
            signal.signal(signal.SIGINT, self._handle_os_signal)
        except (ValueError, AttributeError):
            # Non-main thread or unsupported environment (e.g. testing)
            pass

    def _handle_os_signal(self, signum: int, frame: Any) -> None:
        """Handles SIGTERM / SIGINT by draining the pool."""
        logger.info(f"Signal {signum} intercepted. Initiating graceful worker pool drain.")
        self.shutdown(wait=True)

    def start(self) -> None:
        """Spawns and starts the worker threads."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._is_shutdown = False

            for i in range(self.worker_count):
                thread = threading.Thread(
                    target=self._worker_loop,
                    name=f"LedgerCoreWorker-{i+1}",
                    daemon=True,
                )
                self._workers.append(thread)
                thread.start()

    def _worker_loop(self) -> None:
        """Worker thread loop: sleeps when idle, wakes up on condition signal."""
        while True:
            task: Callable[[], Any] | None = None
            with self._not_empty:
                while not self._queue and not self._is_shutdown:
                    self._not_empty.wait()

                if self._is_shutdown and not self._queue:
                    # Queue drained and shutdown requested
                    break

                if self._queue:
                    task = self._queue.popleft()
                    self._not_full.notify()

            if task is not None:
                try:
                    task()
                except Exception as exc:
                    logger.error(f"Unhandled exception in worker pool task: {exc}", exc_info=True)

    def submit(self, task: Callable[[], Any], timeout: float | None = None) -> bool:
        """Enqueues a task for execution. Blocks if queue is full until timeout."""
        with self._not_full:
            if not self._is_running or self._is_shutdown:
                return False

            if len(self._queue) >= self.max_queue_size:
                if not self._not_full.wait(timeout=timeout):
                    return False

            self._queue.append(task)
            self._not_empty.notify()
            return True

    def enqueue(self, task: Callable[[], Any], timeout: float | None = None) -> bool:
        """Alias for submit."""
        return self.submit(task, timeout=timeout)

    def shutdown(self, wait: bool = True, timeout: float | None = None) -> None:
        """Closes queue to new tasks, signals workers, and drains remaining work."""
        with self._not_empty:
            self._is_shutdown = True
            self._is_running = False
            self._not_empty.notify_all()
            self._not_full.notify_all()

        if wait:
            for thread in self._workers:
                thread.join(timeout=timeout)
            self._workers.clear()

    def drain(self, timeout: float | None = None) -> None:
        """Alias for shutdown(wait=True)."""
        self.shutdown(wait=True, timeout=timeout)

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    @property
    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)
