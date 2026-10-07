"""Verification test suite for in-memory persistence adapters."""

import time
from decimal import Decimal

import pytest

from app.domain.models import Account, LedgerEntry
from app.domain.value_objects import Money
from app.infrastructure.persistence import (
    InMemoryAccountRepository,
    InMemoryIdempotencyStore,
    InMemoryLedgerRepository,
)


class TestInMemoryAccountRepository:
    """Verifies thread-safe account operations and defensive copy isolation."""

    def test_account_crud_and_copy_isolation(self) -> None:
        """Modifying an external account instance does not silently mutate stored state."""
        repo = InMemoryAccountRepository()
        acc = Account("A-1", "Alice", Money(Decimal("100.00"), "BDT"))
        repo.save(acc)

        # Mutate external object
        acc.balance = Money(Decimal("500.00"), "BDT")

        # Stored state in repo must remain intact (copy isolation)
        retrieved = repo.get_by_id("A-1")
        assert retrieved is not None
        assert retrieved.balance == Money(Decimal("100.00"), "BDT")

    def test_update_balance_and_get_all(self) -> None:
        """Tests balance mutation and full listing."""
        repo = InMemoryAccountRepository()
        repo.save(Account("A-1", "Alice", Money(Decimal("100.00"), "BDT")))
        repo.save(Account("A-2", "Bob", Money(Decimal("200.00"), "BDT")))

        repo.update_balance("A-1", Money(Decimal("150.00"), "BDT"))
        assert repo.get_by_id("A-1").balance == Money(Decimal("150.00"), "BDT")  # type: ignore[union-attr]

        all_accounts = repo.get_all()
        assert len(all_accounts) == 2

        with pytest.raises(KeyError):
            repo.update_balance("MISSING", Money(Decimal("10.00"), "BDT"))

        repo.clear()
        assert len(repo.get_all()) == 0


class TestInMemoryLedgerRepository:
    """Verifies append-only storage and net system calculation."""

    def test_ledger_append_and_net_calculation(self) -> None:
        """Tests append, filtering, and total net aggregation."""
        repo = InMemoryLedgerRepository()
        e1 = LedgerEntry.create_debit("E-1", "TX-1", "A-1", Decimal("25.00"), Decimal("75.00"))
        e2 = LedgerEntry.create_credit("E-2", "TX-1", "A-2", Decimal("25.00"), Decimal("125.00"))

        repo.append_entries([e1, e2])

        assert len(repo.get_all_entries()) == 2
        assert len(repo.get_entries_for_account("A-1")) == 1
        assert repo.get_total_system_net() == Decimal("0.00")

        repo.clear()
        assert len(repo.get_all_entries()) == 0


class TestInMemoryIdempotencyStore:
    """Verifies atomic key acquisition, result caching, and TTL expiry."""

    def test_idempotency_lifecycle_with_ttl(self) -> None:
        """Tests acquire, in-flight blocking, storing result, and TTL expiry."""
        store = InMemoryIdempotencyStore()
        key = "idemp-key-1"

        # First acquisition
        assert store.try_acquire(key, ttl_seconds=0.05) is True

        # Second acquisition while active is blocked
        assert store.try_acquire(key, ttl_seconds=0.05) is False

        # Store result with short TTL
        store.store_result(key, {"tx": "1"}, ttl_seconds=0.05)
        assert store.get_result(key) == {"tx": "1"}

        time.sleep(0.06)
        # Expired: returns None and allows new acquisition
        assert store.get_result(key) is None
        assert store.try_acquire(key) is True

    def test_release_uncommitted_key(self) -> None:
        """Releasing an in-flight key allows immediate re-acquisition."""
        store = InMemoryIdempotencyStore()
        key = "fail-key"

        assert store.try_acquire(key) is True
        store.release(key)
        assert store.try_acquire(key) is True
