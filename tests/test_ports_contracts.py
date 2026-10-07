"""Contract verification test suite for Phase 2: Ports & Architectural Contracts.

Proves that:
1. Abstract port contracts strictly prevent partial or direct instantiation (TypeError).
2. Clean in-memory implementations satisfy all Port contracts without framework coupling.
3. Default helper methods on ports operate deterministically across implementations.
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Any

import pytest

from app.domain.models import Account, EntryType, LedgerEntry
from app.domain.value_objects import Money
from app.ports import (
    IAccountRepository,
    ICachePort,
    IEventPublisher,
    IIdempotencyStore,
    ILedgerRepository,
    TransactionSettledEvent,
)

# ============================================================================
# Dummy Test Adapters for Contract Verification
# ============================================================================

class InMemoryAccountRepo(IAccountRepository):
    """Minimal in-memory adapter implementing IAccountRepository."""

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}

    def get_by_id(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def save(self, account: Account) -> None:
        self._accounts[account.account_id] = account

    def get_all(self) -> list[Account]:
        return list(self._accounts.values())


class InMemoryLedgerRepo(ILedgerRepository):
    """Minimal in-memory adapter implementing ILedgerRepository."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def append_entry(self, entry: LedgerEntry) -> None:
        self._entries.append(entry)

    def get_entries_for_account(self, account_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if e.account_id == account_id]

    def get_all_entries(self) -> list[LedgerEntry]:
        return list(self._entries)


class InMemoryIdempotencyStore(IIdempotencyStore):
    """Minimal in-memory adapter implementing IIdempotencyStore."""

    def __init__(self) -> None:
        self._in_flight: set[str] = set()
        self._results: dict[str, dict[str, Any]] = {}

    def try_acquire(self, key: str, ttl_seconds: float | int = 120) -> bool:
        if key in self._in_flight or key in self._results:
            return False
        self._in_flight.add(key)
        return True

    def get_result(self, key: str) -> dict[str, Any] | None:
        return self._results.get(key)

    def store_result(
        self,
        key: str,
        result: dict[str, Any],
        ttl_seconds: float | int = 86400,
    ) -> None:
        self._in_flight.discard(key)
        self._results[key] = result

    def release(self, key: str) -> None:
        self._in_flight.discard(key)


class InMemoryCachePort(ICachePort):
    """Minimal in-memory adapter implementing ICachePort."""

    def __init__(self) -> None:
        self._storage: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._storage.get(key)

    def put(self, key: str, value: Any, ttl_seconds: float | int | None = None) -> None:
        self._storage[key] = value

    def delete(self, key: str) -> bool:
        return self._storage.pop(key, None) is not None


class InMemoryEventPublisher(IEventPublisher):
    """Minimal in-memory adapter implementing IEventPublisher."""

    def __init__(self) -> None:
        self.published_events: list[TransactionSettledEvent] = []
        self.generic_events: list[tuple[str, dict[str, Any]]] = []

    def publish(self, event: TransactionSettledEvent) -> None:
        self.published_events.append(event)

    def publish_generic(self, event_type: str, payload: dict[str, Any]) -> None:
        self.generic_events.append((event_type, payload))


# ============================================================================
# Contract Tests
# ============================================================================

class TestAbstractPortInstantiationRules:
    """Verifies Python abc.ABC strictly enforces abstract method implementation."""

    def test_cannot_instantiate_abstract_ports_directly(self) -> None:
        """Abstract port contracts raise TypeError if instantiated without concrete methods."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IAccountRepository()  # type: ignore[abstract]

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            ILedgerRepository()  # type: ignore[abstract]

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IIdempotencyStore()  # type: ignore[abstract]

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            ICachePort()  # type: ignore[abstract]

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IEventPublisher()  # type: ignore[abstract]


class TestAccountRepositoryContract:
    """Verifies IAccountRepository port contract and default methods."""

    def test_account_repository_crud_lifecycle(self) -> None:
        """Tests save, get_by_id, create, get_all, and update_balance."""
        repo: IAccountRepository = InMemoryAccountRepo()
        acc = Account("ACC-1", "Alice", Money(Decimal("200.00"), "BDT"))

        # Save and query
        repo.save(acc)
        retrieved = repo.get_by_id("ACC-1")
        assert retrieved is not None
        assert retrieved.account_id == "ACC-1"
        assert retrieved.balance == Money(Decimal("200.00"), "BDT")

        # Missing query
        assert repo.get_by_id("NON_EXISTENT") is None

        # Create helper
        acc2 = Account("ACC-2", "Bob", Money(Decimal("100.00"), "BDT"))
        created = repo.create(acc2)
        assert created.account_id == "ACC-2"
        assert len(repo.get_all()) == 2

        # Update balance helper
        repo.update_balance("ACC-1", Money(Decimal("350.00"), "BDT"))
        updated = repo.get_by_id("ACC-1")
        assert updated is not None
        assert updated.balance == Money(Decimal("350.00"), "BDT")

        # Update balance on non-existent account raises KeyError
        with pytest.raises(KeyError, match="Account MISSING not found"):
            repo.update_balance("MISSING", Money(Decimal("10.00"), "BDT"))


class TestLedgerRepositoryContract:
    """Verifies ILedgerRepository port contract, multi-append, and net calculation."""

    def test_ledger_repository_append_and_net_aggregation(self) -> None:
        """Tests append_entry, append_entries, filtering, and get_total_system_net."""
        repo: ILedgerRepository = InMemoryLedgerRepo()

        e1 = LedgerEntry.create_debit("E1", "TX1", "ACC-A", Decimal("50.00"), Decimal("150.00"))
        e2 = LedgerEntry.create_credit("E2", "TX1", "ACC-B", Decimal("50.00"), Decimal("250.00"))

        repo.append_entry(e1)
        repo.append_entries([e2])

        all_entries = repo.get_all_entries()
        assert len(all_entries) == 2

        # Account filtering
        a_entries = repo.get_entries_for_account("ACC-A")
        assert len(a_entries) == 1
        assert a_entries[0].entry_type == EntryType.DEBIT

        # get_entries_by_account alias
        b_entries = repo.get_entries_by_account("ACC-B")
        assert len(b_entries) == 1
        assert b_entries[0].entry_type == EntryType.CREDIT

        # Zero-sum net aggregation: (-50.00) + (+50.00) == 0.00
        assert repo.get_total_system_net() == Decimal("0.00")


class TestIdempotencyStoreContract:
    """Verifies IIdempotencyStore key acquisition, retrieval, and release."""

    def test_idempotency_store_workflow(self) -> None:
        """Tests try_acquire lock, result caching, duplicate rejection, and lock release."""
        store: IIdempotencyStore = InMemoryIdempotencyStore()
        key = "idem-uuid-101"

        # First acquisition succeeds
        assert store.try_acquire(key, ttl_seconds=60) is True

        # Second acquisition while in-flight is rejected
        assert store.try_acquire(key, ttl_seconds=60) is False

        # Result is not yet stored
        assert store.get_result(key) is None

        # Store completed transaction result
        payload = {"status": "SETTLED", "tx_id": "TX-99"}
        store.store_result(key, payload, ttl_seconds=3600)

        # Subsequent queries return cached result
        assert store.get_result(key) == payload

        # Further acquire attempts remain blocked
        assert store.try_acquire(key) is False

    def test_idempotency_store_lock_release_on_failure(self) -> None:
        """Releasing an in-flight key allows subsequent acquisition."""
        store: IIdempotencyStore = InMemoryIdempotencyStore()
        key = "idem-retry-202"

        assert store.try_acquire(key) is True
        assert store.try_acquire(key) is False

        # Release in-flight lock after failure
        store.release(key)

        # Can acquire again after release
        assert store.try_acquire(key) is True


class TestCachePortContract:
    """Verifies ICachePort get, put, delete, and exists contracts."""

    def test_cache_port_lifecycle(self) -> None:
        """Tests put, get, exists, and delete."""
        cache: ICachePort = InMemoryCachePort()

        assert cache.get("missing_key") is None
        assert cache.exists("missing_key") is False

        cache.put("user_session", {"user": "saimon"}, ttl_seconds=300)
        assert cache.exists("user_session") is True
        assert cache.get("user_session") == {"user": "saimon"}

        assert cache.delete("user_session") is True
        assert cache.exists("user_session") is False
        assert cache.delete("user_session") is False


class TestEventPublisherContract:
    """Verifies IEventPublisher domain event publication and event immutability."""

    def test_event_publication_and_event_immutability(self) -> None:
        """Tests publishing TransactionSettledEvent and generic payloads."""
        publisher: IEventPublisher = InMemoryEventPublisher()

        event = TransactionSettledEvent(
            transaction_id="TX-555",
            from_account_id="ACC-SENDER",
            to_account_id="ACC-RECEIVER",
            amount=Money(Decimal("100.00"), "BDT"),
            fee=Money(Decimal("1.50"), "BDT"),
        )
        assert event.transaction_id == "TX-555"
        assert event.amount == Money(Decimal("100.00"), "BDT")
        assert event.fee == Money(Decimal("1.50"), "BDT")
        assert isinstance(event.timestamp, float)

        # Immutability check
        with pytest.raises(FrozenInstanceError):
            event.transaction_id = "MUTATED"  # type: ignore[misc]

        # Publish event
        publisher.publish(event)
        assert len(publisher.published_events) == 1  # type: ignore[attr-defined]
        assert publisher.published_events[0] == event  # type: ignore[attr-defined]

        # Publish generic event
        publisher.publish_generic("ACCOUNT_FROZEN", {"account_id": "ACC-123"})
        assert len(publisher.generic_events) == 1  # type: ignore[attr-defined]
