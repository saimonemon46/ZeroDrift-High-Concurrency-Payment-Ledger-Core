"""Test suite for idempotency and circuit breaker application decorators."""

import time
from typing import Any

import pytest

from app.application.decorators.circuit_decorator import (
    circuit_protected,
)
from app.application.decorators.idempotency_decorator import (
    idempotent,
)
from app.application.exceptions import IdempotencyConflictError
from app.domain.exceptions import InsufficientFundsError
from app.infrastructure.persistence.memory_repositories import InMemoryIdempotencyStore
from app.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)


class TestIdempotencyDecorator:
    """Verifies once-and-only-once execution semantics across network retries."""

    def test_first_call_executes_and_caches_result(self) -> None:
        """First invocation executes and stores the result."""
        store = InMemoryIdempotencyStore()
        call_count = 0

        @idempotent(store=store)
        def process_payment(idempotency_key: str, amount: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return {"status": "SUCCESS", "amount": amount}

        res1 = process_payment(idempotency_key="key-1", amount="100.00")
        assert call_count == 1
        assert res1 == {"status": "SUCCESS", "amount": "100.00"}

        # Second invocation with same key returns cached result without incrementing call_count
        res2 = process_payment(idempotency_key="key-1", amount="100.00")
        assert call_count == 1
        assert res2 == {"status": "SUCCESS", "amount": "100.00"}

    def test_concurrent_in_flight_call_raises_idempotency_conflict(self) -> None:
        """If a key is actively in-flight, duplicate call raises IdempotencyConflictError."""
        store = InMemoryIdempotencyStore()
        store.try_acquire("in-flight-key", ttl_seconds=60)

        @idempotent(store=store)
        def transfer_money(idempotency_key: str) -> dict[str, str]:
            return {"status": "DONE"}

        with pytest.raises(IdempotencyConflictError) as exc_info:
            transfer_money(idempotency_key="in-flight-key")

        assert exc_info.value.key == "in-flight-key"

    def test_failed_call_releases_key_so_retry_succeeds(self) -> None:
        """Exceptions in the target function release the lock allowing subsequent retry."""
        store = InMemoryIdempotencyStore()
        should_fail = True

        @idempotent(store=store)
        def fragile_action(idempotency_key: str) -> dict[str, str]:
            if should_fail:
                raise RuntimeError("Temporary upstream glitch")
            return {"status": "RECOVERED"}

        # Attempt 1: Fails
        with pytest.raises(RuntimeError):
            fragile_action(idempotency_key="retry-key")

        # Lock was released on failure; Attempt 2 should be allowed to run
        should_fail = False
        res = fragile_action(idempotency_key="retry-key")
        assert res == {"status": "RECOVERED"}

    def test_call_without_idempotency_key_passes_through(self) -> None:
        """Calls without an idempotency key execute unconditionally."""
        store = InMemoryIdempotencyStore()
        count = 0

        @idempotent(store=store)
        def unkeyed_action() -> int:
            nonlocal count
            count += 1
            return count

        assert unkeyed_action() == 1
        assert unkeyed_action() == 2
        assert unkeyed_action() == 3


class TestCircuitBreakerDecorator:
    """Verifies automated fail-fast protection and domain error filtering."""

    def test_healthy_execution_succeeds(self) -> None:
        """Healthy target function executes normally under circuit breaker."""
        breaker = CircuitBreaker(name="test_breaker")

        @circuit_protected(circuit_breaker=breaker)
        def healthy_function(x: int) -> int:
            return x * 2

        assert healthy_function(5) == 10

    def test_domain_error_does_not_trip_circuit_breaker(self) -> None:
        """Business validation rejections (e.g. InsufficientFundsError) do not trip breaker."""
        breaker = CircuitBreaker(
            name="test_breaker",
            failure_threshold_rate=0.5,
            min_requests=3,
        )

        @circuit_protected(circuit_breaker=breaker)
        def validate_solvency() -> None:
            raise InsufficientFundsError("Sender has zero balance.")

        # Trigger multiple domain errors
        for _ in range(5):
            with pytest.raises(InsufficientFundsError):
                validate_solvency()

        # Breaker must STILL be healthy (CLOSED) because user business errors != system failure
        assert breaker.can_execute() is True

    def test_infrastructure_error_trips_circuit_breaker_to_open(self) -> None:
        """Unhandled system exceptions trip breaker to OPEN, failing fast in <1ms."""
        breaker = CircuitBreaker(
            name="db_breaker",
            failure_threshold_rate=0.5,
            min_requests=4,
            recovery_timeout_seconds=5.0,
        )

        @circuit_protected(circuit_breaker=breaker)
        def database_query() -> None:
            raise ConnectionError("Database cluster unreachable")

        # Trigger 4 infrastructure failures
        for _ in range(4):
            with pytest.raises(ConnectionError):
                database_query()

        # Breaker should now be OPEN
        assert breaker.can_execute() is False

        # Next invocation must fail fast in < 1ms with CircuitBreakerOpenError
        t0 = time.perf_counter()
        with pytest.raises(CircuitBreakerOpenError):
            database_query()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 1.0  # < 1 millisecond fail-fast guarantee
