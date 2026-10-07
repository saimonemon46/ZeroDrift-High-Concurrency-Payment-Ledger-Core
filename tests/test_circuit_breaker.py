"""Verification test suite for 3-State Circuit Breaker."""

import time

import pytest

from app.infrastructure.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)


class TestCircuitBreaker:
    """Verifies 3-state automaton (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)."""

    def test_initial_state_is_closed(self) -> None:
        """Circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(name="test_cb")
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_successful_executions_remain_closed(self) -> None:
        """Successes record cleanly without tripping."""
        cb = CircuitBreaker(name="test_cb")

        def succeed() -> str:
            return "OK"

        for _ in range(10):
            result = cb.execute(succeed)
            assert result == "OK"

        assert cb.state == CircuitState.CLOSED

    def test_trips_to_open_when_failure_threshold_breached(self) -> None:
        """Breaching error threshold (>50% failures out of min 5) trips to OPEN."""
        cb = CircuitBreaker(
            name="test_cb",
            min_requests=5,
            failure_threshold_rate=0.5,
            recovery_timeout_seconds=0.1,
        )

        def fail() -> None:
            raise RuntimeError("Downstream outage")

        # 3 failures out of 5 = 60% failure rate (>50%)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

        # Attempting execution fails fast with CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError, match="is OPEN"):
            cb.execute(lambda: "never_called")

    def test_half_open_recovery_and_reset_to_closed(self) -> None:
        """After recovery timeout, canary successes transition HALF_OPEN back to CLOSED."""
        cb = CircuitBreaker(
            name="test_cb",
            min_requests=5,
            failure_threshold_rate=0.5,
            recovery_timeout_seconds=0.05,
            half_open_success_threshold=2,
        )

        # Force trip to OPEN
        for _ in range(5):
            cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.06)
        assert cb.get_state() == CircuitState.HALF_OPEN

        # First canary success
        cb.record_success()
        assert cb.get_state() == CircuitState.HALF_OPEN

        # Second canary success completes recovery
        cb.record_success()
        assert cb.get_state() == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_half_open_failure_immediately_retrips_to_open(self) -> None:
        """A failure during HALF_OPEN trips immediately back to OPEN."""
        cb = CircuitBreaker(
            name="test_cb",
            min_requests=5,
            failure_threshold_rate=0.5,
            recovery_timeout_seconds=0.05,
        )

        for _ in range(5):
            cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN

        time.sleep(0.06)
        assert cb.get_state() == CircuitState.HALF_OPEN

        # Canary probe fails
        cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN

    def test_manual_reset(self) -> None:
        """reset() returns circuit to CLOSED immediately."""
        cb = CircuitBreaker(name="test_cb")
        for _ in range(10):
            cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN

        cb.reset()
        assert cb.get_state() == CircuitState.CLOSED
