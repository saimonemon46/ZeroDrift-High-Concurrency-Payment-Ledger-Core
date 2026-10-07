"""3-State Circuit Breaker Automaton for downstream service fault isolation.

State Machine:
CLOSED: Normal operation. Errors recorded in a sliding time window.
OPEN: Tripped when error rate exceeds threshold. Fails fast in <1ms without network calls.
HALF_OPEN: Cool-down elapsed. Allows trial canary probe to test downstream health.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar

from app.domain.exceptions import DomainError

T = TypeVar("T")


class CircuitState(StrEnum):
    """Finite states of the Circuit Breaker automaton."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(DomainError):
    """Raised when an operation is attempted while the Circuit Breaker is OPEN."""

    def __init__(
        self,
        breaker_name: str,
        retry_after_seconds: float,
        message: str | None = None,
    ) -> None:
        self.breaker_name = breaker_name
        self.retry_after_seconds = retry_after_seconds
        msg = (
            message
            or f"Circuit breaker '{breaker_name}' is OPEN. Retry in {retry_after_seconds:.2f}s."
        )
        super().__init__(msg)


class CircuitBreaker:
    """Thread-safe 3-state Circuit Breaker implementing the Resilience automaton."""

    def __init__(
        self,
        name: str = "default_circuit_breaker",
        failure_threshold_rate: float = 0.5,
        min_requests: int = 5,
        sliding_window_seconds: float = 10.0,
        recovery_timeout_seconds: float = 5.0,
        half_open_success_threshold: int = 2,
    ) -> None:
        self.name: str = name
        self.failure_threshold_rate: float = failure_threshold_rate
        self.min_requests: int = min_requests
        self.sliding_window_seconds: float = sliding_window_seconds
        self.recovery_timeout_seconds: float = recovery_timeout_seconds
        self.half_open_success_threshold: int = half_open_success_threshold

        self._state: CircuitState = CircuitState.CLOSED
        self._lock: threading.RLock = threading.RLock()

        # Sliding window history of (timestamp, is_success)
        self._window: deque[tuple[float, bool]] = deque()
        self._tripped_at: float = 0.0
        self._half_open_successes: int = 0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluate_state_transition()
            return self._state

    def get_state(self) -> CircuitState:
        """Returns the current circuit state."""
        return self.state

    def _evaluate_state_transition(self) -> None:
        """Evaluates whether an OPEN circuit should transition to HALF_OPEN."""
        now = time.time()
        if self._state == CircuitState.OPEN:
            if now - self._tripped_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_successes = 0

    def _prune_window(self, now: float) -> None:
        """Discards samples outside the sliding time window."""
        cutoff = now - self.sliding_window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def can_execute(self) -> bool:
        """Determines if a request can proceed without executing it."""
        with self._lock:
            self._evaluate_state_transition()
            return self._state != CircuitState.OPEN

    def record_success(self) -> None:
        """Records a successful operation."""
        with self._lock:
            now = time.time()
            self._evaluate_state_transition()

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.half_open_success_threshold:
                    # Recovery confirmed
                    self._state = CircuitState.CLOSED
                    self._window.clear()
            elif self._state == CircuitState.CLOSED:
                self._prune_window(now)
                self._window.append((now, True))

    def record_failure(self) -> None:
        """Records an operational failure."""
        with self._lock:
            now = time.time()
            self._evaluate_state_transition()

            if self._state == CircuitState.HALF_OPEN:
                # Canary probe failed: trip immediately back to OPEN
                self._state = CircuitState.OPEN
                self._tripped_at = now
                self._half_open_successes = 0
            elif self._state == CircuitState.CLOSED:
                self._prune_window(now)
                self._window.append((now, False))

                total_requests = len(self._window)
                if total_requests >= self.min_requests:
                    failures = sum(1 for _, success in self._window if not success)
                    failure_rate = failures / total_requests
                    if failure_rate >= self.failure_threshold_rate:
                        self._state = CircuitState.OPEN
                        self._tripped_at = now

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Executes a callable under circuit breaker protection.

        Fails fast in <1ms with CircuitBreakerOpenError if circuit is OPEN.
        """
        with self._lock:
            self._evaluate_state_transition()
            if self._state == CircuitState.OPEN:
                elapsed = time.time() - self._tripped_at
                remaining = max(0.0, self.recovery_timeout_seconds - elapsed)
                raise CircuitBreakerOpenError(
                    breaker_name=self.name,
                    retry_after_seconds=remaining,
                )

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def reset(self) -> None:
        """Manually forces the circuit breaker to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._window.clear()
            self._tripped_at = 0.0
            self._half_open_successes = 0
