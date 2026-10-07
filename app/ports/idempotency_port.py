"""Idempotency Store Port for LedgerCore.

Defines the contract for distributed idempotency management, preventing duplicate
financial mutations and mobile retry storm double-charges.
"""

from abc import ABC, abstractmethod
from typing import Any


class IIdempotencyStore(ABC):
    """Abstract port for idempotency key acquisition and result caching."""

    @abstractmethod
    def try_acquire(self, key: str, ttl_seconds: float | int = 120) -> bool:
        """Attempts to atomically acquire an idempotency lock for the key.

        Args:
            key: Unique idempotency token (e.g. UUIDv4).
            ttl_seconds: In-flight lock expiration in seconds.

        Returns:
            bool: True if lock acquired (first attempt), False if in-flight or completed.
        """
        pass

    @abstractmethod
    def get_result(self, key: str) -> dict[str, Any] | None:
        """Retrieves previously cached execution result if completed.

        Args:
            key: Unique idempotency token.

        Returns:
            Optional dict containing execution result or None.
        """
        pass

    @abstractmethod
    def store_result(
        self,
        key: str,
        result: dict[str, Any],
        ttl_seconds: float | int = 86400,
    ) -> None:
        """Stores final execution result for the idempotency key.

        Args:
            key: Unique idempotency token.
            result: Result dictionary to be returned on duplicate requests.
            ttl_seconds: Cache duration (default 24 hours).
        """
        pass

    @abstractmethod
    def release(self, key: str) -> None:
        """Releases an in-flight idempotency lock in the event of an uncommitted failure."""
        pass
