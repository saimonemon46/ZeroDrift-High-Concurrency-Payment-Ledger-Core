"""Cache Port for LedgerCore.

Defines the contract for high-speed key-value cache lookups.
"""

from abc import ABC, abstractmethod
from typing import Any


class ICachePort(ABC):
    """Abstract port for key-value caching."""

    @abstractmethod
    def get(self, key: str) -> Any | None:
        """Retrieves a cached value by key. Returns None if absent or expired."""
        pass

    @abstractmethod
    def put(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Stores a key-value pair with an optional Time-To-Live in seconds."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Deletes a key from cache. Returns True if existed, False otherwise."""
        pass

    def exists(self, key: str) -> bool:
        """Checks if a key exists in cache without reading value."""
        return self.get(key) is not None
