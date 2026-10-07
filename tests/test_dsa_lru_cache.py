"""Verification test suite for custom O(1) LRUTTLCache."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.infrastructure.dsa import LRUTTLCache


class TestLRUTTLCache:
    """Verifies O(1) lookup, LRU eviction policy, dual TTL mechanics, and thread safety."""

    def test_cache_initialization_and_capacity_validation(self) -> None:
        """Cache validates positive capacity."""
        cache = LRUTTLCache(capacity=5)
        assert cache.capacity == 5
        assert cache.size() == 0

        with pytest.raises(ValueError, match="strictly positive"):
            LRUTTLCache(capacity=0)

    def test_put_get_and_cache_miss(self) -> None:
        """Basic put and get operations."""
        cache = LRUTTLCache(capacity=3)
        cache.put("k1", "v1")
        cache.put("k2", "v2")

        assert cache.get("k1") == "v1"
        assert cache.get("k2") == "v2"
        assert cache.get("k3") is None
        assert cache.size() == 2

    def test_lru_eviction_policy(self) -> None:
        """When capacity is reached, least recently used item is evicted."""
        cache = LRUTTLCache(capacity=3)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.put("C", 3)

        # Access A so B becomes the least recently used
        assert cache.get("A") == 1

        # Insert D: should evict B
        cache.put("D", 4)

        assert cache.get("B") is None  # Evicted
        assert cache.get("A") == 1
        assert cache.get("C") == 3
        assert cache.get("D") == 4

    def test_updating_existing_key(self) -> None:
        """Updating an existing key updates value and refreshes recency without duplicating."""
        cache = LRUTTLCache(capacity=2)
        cache.put("A", 10)
        cache.put("B", 20)

        cache.put("A", 99)  # Update A
        assert cache.get("A") == 99
        assert cache.size() == 2

        # Insert C: should evict B (since A was refreshed)
        cache.put("C", 30)
        assert cache.get("B") is None
        assert cache.get("A") == 99
        assert cache.get("C") == 30

    def test_delete_and_exists(self) -> None:
        """Verifies delete and exists operations."""
        cache = LRUTTLCache(capacity=3)
        cache.put("X", "val_x")

        assert cache.exists("X") is True
        assert cache.exists("Y") is False

        assert cache.delete("X") is True
        assert cache.exists("X") is False
        assert cache.get("X") is None
        assert cache.delete("X") is False

    def test_lazy_ttl_expiration_on_access(self) -> None:
        """Expired items are lazily purged upon get() and exists()."""
        cache = LRUTTLCache(capacity=5)
        cache.put("temp", "expiring_val", ttl_seconds=0.05)

        assert cache.get("temp") == "expiring_val"
        time.sleep(0.06)

        # Lazy eviction upon access
        assert cache.get("temp") is None
        assert cache.exists("temp") is False
        assert cache.size() == 0

    def test_active_ttl_purge_sweep(self) -> None:
        """Active sweep purges all expired nodes and returns count."""
        cache = LRUTTLCache(capacity=10)
        cache.put("e1", "val1", ttl_seconds=0.05)
        cache.put("e2", "val2", ttl_seconds=0.05)
        cache.put("alive", "val3", ttl_seconds=10.0)

        time.sleep(0.06)
        purged = cache.purge_expired()
        assert purged == 2
        assert cache.size() == 1
        assert cache.get("alive") == "val3"

    def test_concurrent_read_write_thread_safety(self) -> None:
        """Multi-threaded stress test ensuring zero race condition exceptions."""
        cache = LRUTTLCache(capacity=50)

        def worker(thread_id: int) -> None:
            for i in range(100):
                key = f"k_{i % 20}"
                cache.put(key, f"thread_{thread_id}_val_{i}")
                _ = cache.get(key)
                _ = cache.exists(key)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(8)]
            for f in futures:
                f.result(timeout=5.0)

        assert cache.size() <= 50
