"""Idempotency Decorator and Wrapper ensuring once-and-only-once execution."""

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from app.application.exceptions import IdempotencyConflictError
from app.application.use_cases.execute_transfer import TransferResult
from app.ports.idempotency_port import IIdempotencyStore

T = TypeVar("T")


def _extract_idempotency_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    """Extracts idempotency key from arguments or TransferCommand."""
    if "idempotency_key" in kwargs and kwargs["idempotency_key"]:
        return str(kwargs["idempotency_key"])

    if "key" in kwargs and kwargs["key"]:
        return str(kwargs["key"])

    for arg in args:
        if hasattr(arg, "idempotency_key") and arg.idempotency_key:
            return str(arg.idempotency_key)
        if isinstance(arg, dict) and "idempotency_key" in arg:
            return str(arg["idempotency_key"])

    return None


class IdempotencyWrapper:
    """Wraps any use case or callable with atomic idempotency semantics."""

    def __init__(
        self,
        target: Callable[..., Any],
        store: IIdempotencyStore,
        in_flight_ttl_seconds: float | int = 120,
        result_ttl_seconds: float | int = 86400,
    ) -> None:
        self.target = target
        self.store = store
        self.in_flight_ttl_seconds = in_flight_ttl_seconds
        self.result_ttl_seconds = result_ttl_seconds

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        key = _extract_idempotency_key(args, kwargs)

        # Passthrough if no idempotency key provided
        if not key:
            return self.target(*args, **kwargs)

        # 1. Fast-path: Return cached completed execution
        cached = self.store.get_result(key)
        if cached is not None:
            if "transaction_id" in cached and "from_balance_after" in cached:
                return TransferResult.from_dict(cached)
            return cached

        # 2. Acquire in-flight distributed lock
        acquired = self.store.try_acquire(key, ttl_seconds=self.in_flight_ttl_seconds)
        if not acquired:
            # Re-check in case concurrent worker completed in the intervening microsecond
            cached = self.store.get_result(key)
            if cached is not None:
                if "transaction_id" in cached and "from_balance_after" in cached:
                    return TransferResult.from_dict(cached)
                return cached
            raise IdempotencyConflictError(key)

        # 3. Execute target function
        try:
            result = self.target(*args, **kwargs)
        except Exception:
            # Release lock so subsequent retries are not blocked by a failed execution
            self.store.release(key)
            raise

        # 4. Cache successful result
        serialized: dict[str, Any]
        if hasattr(result, "to_dict"):
            serialized = result.to_dict()
        elif isinstance(result, dict):
            serialized = result
        else:
            serialized = {"result": result}

        self.store.store_result(key, serialized, ttl_seconds=self.result_ttl_seconds)
        return result


def idempotent(
    store: IIdempotencyStore,
    in_flight_ttl_seconds: float | int = 120,
    result_ttl_seconds: float | int = 86400,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Function decorator applying idempotency protection to a callable."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        wrapper = IdempotencyWrapper(
            target=func,
            store=store,
            in_flight_ttl_seconds=in_flight_ttl_seconds,
            result_ttl_seconds=result_ttl_seconds,
        )

        @wraps(func)
        def inner(*args: Any, **kwargs: Any) -> T:
            return wrapper(*args, **kwargs)  # type: ignore[no-any-return]

        return inner

    return decorator
