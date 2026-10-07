"""Binary Min-Heap Priority Queue for scheduled retry operations.

Maintains tasks ordered by scheduled execution timestamps to guarantee O(log N)
enqueue operations and O(1) earliest candidate inspection without polling all tasks.
"""

import heapq
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class RetryTask:
    """Scheduled task item compared strictly by execution timestamp."""
    scheduled_at: float
    attempt_count: int = field(compare=False)
    task_id: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)


class PriorityRetryHeap:
    """Thread-safe binary min-heap for retry task scheduling."""

    def __init__(self) -> None:
        self._heap: list[RetryTask] = []
        self._lock: threading.RLock = threading.RLock()

    def push(
        self,
        task_id: str,
        payload: dict[str, Any],
        scheduled_at: float,
        attempt_count: int = 0,
    ) -> None:
        """Pushes a new retry task into the min-heap in O(log N)."""
        task = RetryTask(
            scheduled_at=scheduled_at,
            attempt_count=attempt_count,
            task_id=task_id,
            payload=payload,
        )
        with self._lock:
            heapq.heappush(self._heap, task)

    def peek(self) -> RetryTask | None:
        """Inspects the earliest scheduled task in O(1) without extraction."""
        with self._lock:
            return self._heap[0] if self._heap else None

    def pop_due_tasks(self, current_time: float | None = None) -> list[RetryTask]:
        """Extracts all tasks whose scheduled timestamp has arrived (<= current_time).

        Returns:
            list[RetryTask]: Chronologically ordered ready tasks.
        """
        now = current_time if current_time is not None else time.time()
        due_tasks: list[RetryTask] = []

        with self._lock:
            while self._heap and self._heap[0].scheduled_at <= now:
                due_tasks.append(heapq.heappop(self._heap))

        return due_tasks

    def size(self) -> int:
        """Returns the number of pending tasks in the heap."""
        with self._lock:
            return len(self._heap)

    def is_empty(self) -> bool:
        """Returns True if no tasks are pending."""
        with self._lock:
            return len(self._heap) == 0

    def clear(self) -> None:
        """Flushes all pending tasks."""
        with self._lock:
            self._heap.clear()

    @staticmethod
    def calculate_exponential_backoff(
        attempt: int,
        base_seconds: float = 1.0,
        max_seconds: float = 60.0,
        full_jitter: bool = True,
    ) -> float:
        """Calculates exponential backoff delay with optional full jitter.

        Formula:
            T = min(max_seconds, base_seconds * 2^attempt)
            Delay = random.uniform(0, T) if full_jitter else T
        """
        ceiling = min(max_seconds, base_seconds * (2 ** attempt))
        if full_jitter:
            return float(random.uniform(0.0, ceiling))
        return float(ceiling)

    def __len__(self) -> int:
        return self.size()
