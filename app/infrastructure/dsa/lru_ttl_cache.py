"""Custom thread-safe O(1) LRU Cache with active and passive TTL expiration.

Constructed from first principles using:
1. Doubly-Linked List with permanent dummy Sentinel HEAD and TAIL nodes.
2. Hash Map (dict[str, _Node]) mapping keys directly to node memory addresses.
3. Dual TTL: Lazy eviction on access + active batch sweep method.
4. Concurrency: Protected by threading.RLock for thread safety.
"""

import threading
import time
from typing import Any

from app.ports.cache_port import ICachePort


class _Node:
    """Internal Doubly-Linked List Node."""

    def __init__(
        self,
        key: str = "",
        value: Any = None,
        expires_at: float | None = None,
    ) -> None:
        self.key: str = key
        self.value: Any = value
        self.expires_at: float | None = expires_at
        self.prev: _Node | None = None
        self.next: _Node | None = None

    def is_expired(self, current_time: float) -> bool:
        """Returns True if the node has a defined expiration time that has elapsed."""
        return self.expires_at is not None and current_time >= self.expires_at


class LRUTTLCache(ICachePort):
    """High-performance thread-safe O(1) LRU Cache with TTL support.

    Implements the ICachePort architectural contract.
    """

    def __init__(
        self,
        capacity: int = 1000,
        default_ttl_seconds: float | int | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"Cache capacity must be strictly positive, got {capacity}.")

        self.capacity: int = capacity
        self.default_ttl_seconds: float | int | None = default_ttl_seconds
        self._lookup: dict[str, _Node] = {}
        self._lock: threading.RLock = threading.RLock()

        # Dummy sentinels
        self._head: _Node = _Node("__HEAD__")
        self._tail: _Node = _Node("__TAIL__")
        self._head.next = self._tail
        self._tail.prev = self._head

    def _remove_node(self, node: _Node) -> None:
        """Detaches node from the doubly-linked list in O(1)."""
        prev_node = node.prev
        next_node = node.next
        if prev_node is not None:
            prev_node.next = next_node
        if next_node is not None:
            next_node.prev = prev_node

    def _add_to_head(self, node: _Node) -> None:
        """Inserts node immediately after head sentinel (Most Recently Used position) in O(1)."""
        first_node = self._head.next
        node.prev = self._head
        node.next = first_node

        self._head.next = node
        if first_node is not None:
            first_node.prev = node

    def _move_to_head(self, node: _Node) -> None:
        """Moves an existing node to the MRU head position in O(1)."""
        self._remove_node(node)
        self._add_to_head(node)

    def _pop_tail(self) -> _Node | None:
        """Evicts and returns the LRU node immediately before tail sentinel in O(1)."""
        lru_node = self._tail.prev
        if lru_node is not None and lru_node is not self._head:
            self._remove_node(lru_node)
            return lru_node
        return None

    def get(self, key: str) -> Any | None:
        """Retrieves cached value in O(1). Performs lazy eviction if expired."""
        with self._lock:
            node = self._lookup.get(key)
            if node is None:
                return None

            now = time.time()
            if node.is_expired(now):
                # Lazy eviction
                self._remove_node(node)
                del self._lookup[key]
                return None

            self._move_to_head(node)
            return node.value

    def put(self, key: str, value: Any, ttl_seconds: float | int | None = None) -> None:
        """Inserts or updates a cache key-value pair in O(1)."""
        with self._lock:
            effective_ttl = (
                ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
            )
            expires_at = (
                time.time() + effective_ttl if effective_ttl is not None else None
            )

            if key in self._lookup:
                node = self._lookup[key]
                node.value = value
                node.expires_at = expires_at
                self._move_to_head(node)
                return

            new_node = _Node(key=key, value=value, expires_at=expires_at)
            self._lookup[key] = new_node
            self._add_to_head(new_node)

            # Evict LRU element if capacity exceeded
            if len(self._lookup) > self.capacity:
                evicted = self._pop_tail()
                if evicted is not None and evicted.key in self._lookup:
                    del self._lookup[evicted.key]

    def delete(self, key: str) -> bool:
        """Removes a key from cache in O(1). Returns True if deleted."""
        with self._lock:
            node = self._lookup.get(key)
            if node is None:
                return False

            self._remove_node(node)
            del self._lookup[key]
            return True

    def exists(self, key: str) -> bool:
        """Evaluates whether key exists in cache without resetting LRU recency position."""
        with self._lock:
            node = self._lookup.get(key)
            if node is None:
                return False

            if node.is_expired(time.time()):
                self._remove_node(node)
                del self._lookup[key]
                return False

            return True

    def purge_expired(self) -> int:
        """Actively sweeps through the cache and purges all expired entries.

        Returns:
            int: Number of expired entries purged.
        """
        with self._lock:
            now = time.time()
            expired_keys: list[str] = [
                k for k, node in self._lookup.items() if node.is_expired(now)
            ]
            for key in expired_keys:
                node = self._lookup[key]
                self._remove_node(node)
                del self._lookup[key]
            return len(expired_keys)

    def size(self) -> int:
        """Returns current number of active items in cache."""
        with self._lock:
            return len(self._lookup)

    def clear(self) -> None:
        """Flushes all entries from cache."""
        with self._lock:
            self._lookup.clear()
            self._head.next = self._tail
            self._tail.prev = self._head

    def __len__(self) -> int:
        return self.size()
