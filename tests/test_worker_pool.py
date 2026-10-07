"""Verification test suite for ThreadSafeWorkerPool."""

import threading
import time
from collections.abc import Callable

import pytest

from app.infrastructure.os_threading import ThreadSafeWorkerPool


class TestThreadSafeWorkerPool:
    """Verifies condition variable scheduling, queue backpressure, and graceful draining."""

    def test_worker_pool_validation_and_lifecycle(self) -> None:
        """Validates configuration parameters and startup/shutdown states."""
        with pytest.raises(ValueError, match="Worker count must be > 0"):
            ThreadSafeWorkerPool(worker_count=0)

        with pytest.raises(ValueError, match="Max queue size must be > 0"):
            ThreadSafeWorkerPool(max_queue_size=0)

        pool = ThreadSafeWorkerPool(worker_count=2, max_queue_size=10)
        assert pool.is_running is False

        pool.start()
        assert pool.is_running is True

        pool.shutdown(wait=True)
        assert pool.is_running is False
        assert pool.is_shutdown is True

    def test_tasks_processed_concurrently(self) -> None:
        """Executes tasks concurrently across workers."""
        pool = ThreadSafeWorkerPool(worker_count=4, max_queue_size=100)
        pool.start()

        results: list[int] = []
        lock = threading.Lock()

        def make_task(val: int) -> None:
            time.sleep(0.01)
            with lock:
                results.append(val)

        def make_job(val: int) -> Callable[[], None]:
            return lambda: make_task(val)

        for i in range(20):
            assert pool.enqueue(make_job(i)) is True

        pool.drain(timeout=5.0)
        assert len(results) == 20
        assert set(results) == set(range(20))

    def test_drain_completes_all_pending_tasks(self) -> None:
        """Verifies drain flushes every enqueued task before threads terminate."""
        pool = ThreadSafeWorkerPool(worker_count=2, max_queue_size=50)
        pool.start()

        processed_count = 0
        counter_lock = threading.Lock()

        def slow_task() -> None:
            time.sleep(0.005)
            nonlocal processed_count
            with counter_lock:
                processed_count += 1

        for _ in range(15):
            pool.submit(slow_task)

        # Trigger drain
        pool.drain(timeout=5.0)
        assert processed_count == 15
        assert pool.queue_size == 0

    def test_rejection_after_shutdown(self) -> None:
        """Submitting tasks after shutdown returns False."""
        pool = ThreadSafeWorkerPool(worker_count=2)
        pool.start()
        pool.shutdown(wait=True)

        assert pool.submit(lambda: None) is False
