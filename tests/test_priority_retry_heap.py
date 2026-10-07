"""Verification test suite for PriorityRetryHeap."""

import time

from app.infrastructure.dsa import PriorityRetryHeap


class TestPriorityRetryHeap:
    """Verifies binary min-heap scheduling invariants and exponential backoff math."""

    def test_push_and_peek_earliest_task(self) -> None:
        """Earliest scheduled task is always at root (peek) regardless of insertion order."""
        heap = PriorityRetryHeap()
        now = time.time()

        heap.push("task-late", {"v": 3}, scheduled_at=now + 50.0)
        heap.push("task-early", {"v": 1}, scheduled_at=now + 10.0)
        heap.push("task-mid", {"v": 2}, scheduled_at=now + 30.0)

        earliest = heap.peek()
        assert earliest is not None
        assert earliest.task_id == "task-early"
        assert heap.size() == 3

    def test_pop_due_tasks_chronological_extraction(self) -> None:
        """Extracts only tasks whose scheduled timestamp is <= now, in chronological order."""
        heap = PriorityRetryHeap()
        base_time = 1000.0

        # Push in reverse / random order
        heap.push("T3", {"order": 3}, scheduled_at=base_time + 3.0)
        heap.push("T1", {"order": 1}, scheduled_at=base_time + 1.0)
        heap.push("T4", {"order": 4}, scheduled_at=base_time + 10.0)  # Future task
        heap.push("T2", {"order": 2}, scheduled_at=base_time + 2.0)

        # Query at base_time + 3.0
        due = heap.pop_due_tasks(current_time=base_time + 3.0)
        assert len(due) == 3
        assert [t.task_id for t in due] == ["T1", "T2", "T3"]

        # Remaining in heap is T4
        assert heap.size() == 1
        assert heap.peek() is not None
        assert heap.peek().task_id == "T4"  # type: ignore[union-attr]

    def test_exponential_backoff_calculation(self) -> None:
        """Tests deterministic and full jitter backoff limits."""
        # Without jitter: base * 2^attempt capped at max
        b0 = PriorityRetryHeap.calculate_exponential_backoff(
            attempt=0, base_seconds=1.0, full_jitter=False
        )
        assert b0 == 1.0

        b1 = PriorityRetryHeap.calculate_exponential_backoff(
            attempt=1, base_seconds=1.0, full_jitter=False
        )
        assert b1 == 2.0

        b3 = PriorityRetryHeap.calculate_exponential_backoff(
            attempt=3, base_seconds=1.0, full_jitter=False
        )
        assert b3 == 8.0

        # Cap test
        b_capped = PriorityRetryHeap.calculate_exponential_backoff(
            attempt=10, base_seconds=1.0, max_seconds=30.0, full_jitter=False
        )
        assert b_capped == 30.0

        # With jitter: 0 <= value <= ceiling
        for attempt in range(5):
            jittered = PriorityRetryHeap.calculate_exponential_backoff(
                attempt=attempt, base_seconds=1.0, max_seconds=60.0, full_jitter=True
            )
            ceiling = min(60.0, 1.0 * (2 ** attempt))
            assert 0.0 <= jittered <= ceiling

    def test_clear_and_is_empty(self) -> None:
        """Verifies clear and empty predicates."""
        heap = PriorityRetryHeap()
        assert heap.is_empty() is True

        heap.push("task", {}, scheduled_at=time.time())
        assert heap.is_empty() is False

        heap.clear()
        assert heap.is_empty() is True
        assert heap.peek() is None
