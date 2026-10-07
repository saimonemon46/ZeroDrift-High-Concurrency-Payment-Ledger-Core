"""Circuit Breaker Decorator providing automated resilience protection."""

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from app.application.exceptions import ApplicationError
from app.domain.exceptions import DomainError
from app.infrastructure.resilience.circuit_breaker import CircuitBreaker

T = TypeVar("T")


class CircuitBreakerWrapper:
    """Wraps use cases with Circuit Breaker resilience, filtering domain errors."""

    def __init__(
        self,
        target: Callable[..., Any],
        circuit_breaker: CircuitBreaker,
        ignored_exceptions: tuple[type[Exception], ...] = (DomainError, ApplicationError),
    ) -> None:
        self.target = target
        self.circuit_breaker = circuit_breaker
        self.ignored_exceptions = ignored_exceptions

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Fails fast in <1ms if circuit is OPEN
        if not self.circuit_breaker.can_execute():
            # Trigger execute to raise CircuitBreakerOpenError with remaining retry time
            return self.circuit_breaker.execute(lambda: None)

        try:
            result = self.target(*args, **kwargs)
            self.circuit_breaker.record_success()
            return result
        except self.ignored_exceptions:
            # Domain and user validation rejections do not degrade system health
            raise
        except Exception:
            # Genuine infrastructure / system failures trip the circuit
            self.circuit_breaker.record_failure()
            raise


def circuit_protected(
    circuit_breaker: CircuitBreaker,
    ignored_exceptions: tuple[type[Exception], ...] = (DomainError, ApplicationError),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Function decorator applying Circuit Breaker protection to a callable."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        wrapper = CircuitBreakerWrapper(
            target=func,
            circuit_breaker=circuit_breaker,
            ignored_exceptions=ignored_exceptions,
        )

        @wraps(func)
        def inner(*args: Any, **kwargs: Any) -> T:
            return wrapper(*args, **kwargs)  # type: ignore[no-any-return]

        return inner

    return decorator
