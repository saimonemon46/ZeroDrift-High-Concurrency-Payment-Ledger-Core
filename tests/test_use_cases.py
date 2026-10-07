"""Comprehensive test suite for LedgerCore application use cases."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

import pytest

from app.application.exceptions import (
    AccountNotFoundError,
    ReconciliationDivergenceError,
    SelfTransferNotAllowedError,
)
from app.application.use_cases.audit_reconciliation import AuditReconciliationUseCase
from app.application.use_cases.execute_transfer import (
    ExecuteTransferUseCase,
    TransferCommand,
    TransferResult,
)
from app.application.use_cases.query_balance import QueryBalanceUseCase
from app.domain.exceptions import (
    AccountFrozenError,
    AccountInactiveError,
    CurrencyMismatchError,
    InsufficientFundsError,
)
from app.domain.models import Account, LedgerEntry
from app.domain.strategies.fee_strategy import FlatFeeStrategy
from app.domain.value_objects import Currency, Money
from app.infrastructure.dsa.lru_ttl_cache import LRUTTLCache
from app.infrastructure.persistence.memory_repositories import (
    InMemoryAccountRepository,
    InMemoryLedgerRepository,
)
from app.ports.event_publisher_port import IEventPublisher, TransactionSettledEvent


class RecordingEventPublisher(IEventPublisher):
    """Test spy capturing published domain events."""

    def __init__(self) -> None:
        self.published_events: list[TransactionSettledEvent] = []

    def publish(self, event: TransactionSettledEvent) -> None:
        self.published_events.append(event)

    def publish_generic(self, event_type: str, payload: dict[str, Any]) -> None:
        pass


class TestExecuteTransferUseCase:
    """Verifies orchestration of atomic, double-entry transfers."""

    def test_successful_transfer_zero_fee(self) -> None:
        """Alice sends $100 to Bob with default zero-fee policy."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()
        publisher = RecordingEventPublisher()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(
            account_repo=account_repo,
            ledger_repo=ledger_repo,
            event_publisher=publisher,
        )

        cmd = TransferCommand(
            from_account_id="ACC-1",
            to_account_id="ACC-2",
            amount=Money(Decimal("100.00"), Currency.USD),
        )
        result = use_case.execute(cmd)

        assert isinstance(result, TransferResult)
        assert result.status == "SETTLED"
        assert result.amount == Money(Decimal("100.00"), Currency.USD)
        assert result.fee == Money(Decimal("0.00"), Currency.USD)
        assert result.from_balance_after == Money(Decimal("400.00"), Currency.USD)
        assert result.to_balance_after == Money(Decimal("150.00"), Currency.USD)

        # Verify state in repository
        alice = account_repo.get_by_id("ACC-1")
        bob = account_repo.get_by_id("ACC-2")
        assert alice is not None and alice.balance == Money(Decimal("400.00"), Currency.USD)
        assert bob is not None and bob.balance == Money(Decimal("150.00"), Currency.USD)

        # Verify double-entry ledger entries
        entries = ledger_repo.get_all_entries()
        assert len(entries) == 2
        debit = next(e for e in entries if e.account_id == "ACC-1")
        credit = next(e for e in entries if e.account_id == "ACC-2")
        assert debit.amount == Decimal("-100.00")
        assert credit.amount == Decimal("100.00")
        assert debit.amount + credit.amount == Decimal("0.00")

        # Verify event was published
        assert len(publisher.published_events) == 1
        assert publisher.published_events[0].transaction_id == result.transaction_id

    def test_successful_transfer_with_commercial_fee(self) -> None:
        """Alice sends $100 to Bob with $1.50 flat fee."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))
        account_repo.save(
            Account("ACC-SYS-FEE", "Treasury", Money(Decimal("1000.00"), Currency.USD))
        )

        use_case = ExecuteTransferUseCase(
            account_repo=account_repo,
            ledger_repo=ledger_repo,
        )

        cmd = TransferCommand(
            from_account_id="ACC-1",
            to_account_id="ACC-2",
            amount=Decimal("100.00"),
            currency=Currency.USD,
            fee_strategy=FlatFeeStrategy(Decimal("1.50")),
        )
        result = use_case.execute(cmd)

        assert result.fee == Money(Decimal("1.50"), Currency.USD)
        assert result.from_balance_after == Money(Decimal("398.50"), Currency.USD)
        assert result.to_balance_after == Money(Decimal("150.00"), Currency.USD)

        fee_acc = account_repo.get_by_id("ACC-SYS-FEE")
        assert fee_acc is not None
        assert fee_acc.balance == Money(Decimal("1001.50"), Currency.USD)

        # Verify 3 ledger entries and zero-sum conservation
        entries = ledger_repo.get_all_entries()
        assert len(entries) == 3
        net = sum((e.amount for e in entries), start=Decimal("0.00"))
        assert net == Decimal("0.00")

    def test_insufficient_funds_rolls_back_and_releases_locks(self) -> None:
        """Attempting transfer beyond balance raises error and does not mutate accounts."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("50.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(
            account_repo=account_repo,
            ledger_repo=ledger_repo,
        )

        cmd = TransferCommand(
            from_account_id="ACC-1",
            to_account_id="ACC-2",
            amount=Money(Decimal("100.00"), Currency.USD),
        )

        with pytest.raises(InsufficientFundsError):
            use_case.execute(cmd)

        # Accounts remain untouched
        alice = account_repo.get_by_id("ACC-1")
        bob = account_repo.get_by_id("ACC-2")
        assert alice is not None and alice.balance == Money(Decimal("50.00"), Currency.USD)
        assert bob is not None and bob.balance == Money(Decimal("50.00"), Currency.USD)
        assert len(ledger_repo.get_all_entries()) == 0

    def test_frozen_account_rollback(self) -> None:
        """Transfer involving a frozen account is rejected with AccountFrozenError."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        alice = Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD))
        alice.freeze()
        account_repo.save(alice)
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)
        cmd = TransferCommand("ACC-1", "ACC-2", Money(Decimal("50.00"), Currency.USD))

        with pytest.raises(AccountFrozenError):
            use_case.execute(cmd)

    def test_closed_account_rollback(self) -> None:
        """Transfer involving a closed account is rejected."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        bob = Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD))
        bob.close()
        account_repo.save(bob)

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)
        cmd = TransferCommand("ACC-1", "ACC-2", Money(Decimal("50.00"), Currency.USD))

        with pytest.raises(AccountInactiveError):
            use_case.execute(cmd)

    def test_currency_mismatch_rollback(self) -> None:
        """Cross-currency transfer without exchange rate conversion is rejected."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("500.00"), Currency.EUR)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)
        cmd = TransferCommand("ACC-1", "ACC-2", Money(Decimal("50.00"), Currency.USD))

        with pytest.raises(CurrencyMismatchError):
            use_case.execute(cmd)

    def test_self_transfer_rejected(self) -> None:
        """Transferring to identical account ID raises SelfTransferNotAllowedError."""
        use_case = ExecuteTransferUseCase(
            InMemoryAccountRepository(),
            InMemoryLedgerRepository(),
        )
        cmd = TransferCommand("ACC-1", "ACC-1", Money(Decimal("50.00"), Currency.USD))

        with pytest.raises(SelfTransferNotAllowedError):
            use_case.execute(cmd)

    def test_nonexistent_account_raises_account_not_found(self) -> None:
        """Transfer with missing account raises AccountNotFoundError."""
        account_repo = InMemoryAccountRepository()
        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        use_case = ExecuteTransferUseCase(account_repo, InMemoryLedgerRepository())

        with pytest.raises(AccountNotFoundError) as exc_info:
            use_case.execute(
                TransferCommand("ACC-1", "ACC-999", Money(Decimal("10.00"), Currency.USD))
            )
        assert exc_info.value.account_id == "ACC-999"

    def test_cache_invalidation_on_settlement(self) -> None:
        """Cache keys for participating accounts are evicted upon transfer."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()
        cache = LRUTTLCache(capacity=10)

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))

        # Pre-populate cache with stale data
        cache.put("ACC-1", {"balance": "500.00"})
        cache.put("ACC-2", {"balance": "50.00"})

        use_case = ExecuteTransferUseCase(
            account_repo=account_repo,
            ledger_repo=ledger_repo,
            cache=cache,
        )

        use_case.execute(
            TransferCommand("ACC-1", "ACC-2", Money(Decimal("100.00"), Currency.USD))
        )

        # Cache must be evicted
        assert cache.get("ACC-1") is None
        assert cache.get("ACC-2") is None

    def test_concurrent_transfers_thread_safety(self) -> None:
        """20 concurrent transfers between Alice and Bob execute safely without deadlocks."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        # Alice: $1000, Bob: $1000
        account_repo.save(Account("ACC-A", "Alice", Money(Decimal("1000.00"), Currency.USD)))
        account_repo.save(Account("ACC-B", "Bob", Money(Decimal("1000.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)

        # 10 transfers A -> B ($10 each) and 10 transfers B -> A ($10 each)
        def transfer_a_to_b() -> TransferResult:
            return use_case.execute(
                TransferCommand("ACC-A", "ACC-B", Money(Decimal("10.00"), Currency.USD))
            )

        def transfer_b_to_a() -> TransferResult:
            return use_case.execute(
                TransferCommand("ACC-B", "ACC-A", Money(Decimal("10.00"), Currency.USD))
            )

        tasks = [transfer_a_to_b if i % 2 == 0 else transfer_b_to_a for i in range(20)]

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(t) for t in tasks]
            results = [f.result() for f in as_completed(futures)]

        assert len(results) == 20
        # Final balances must both be exactly $1000.00
        alice = account_repo.get_by_id("ACC-A")
        bob = account_repo.get_by_id("ACC-B")
        assert alice is not None and alice.balance == Money(Decimal("1000.00"), Currency.USD)
        assert bob is not None and bob.balance == Money(Decimal("1000.00"), Currency.USD)

        # Ledger must have 40 entries with net sum = 0.00
        assert len(ledger_repo.get_all_entries()) == 40
        assert ledger_repo.get_total_system_net() == Decimal("0.00")


class TestAuditReconciliationUseCase:
    """Verifies system-wide mathematical balance and fraud/tamper detection."""

    def test_reconcile_healthy_system(self) -> None:
        """Healthy system with multiple transactions reconciles to is_balanced=True."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)
        use_case.execute(
            TransferCommand("ACC-1", "ACC-2", Money(Decimal("100.00"), Currency.USD))
        )
        use_case.execute(
            TransferCommand("ACC-1", "ACC-2", Money(Decimal("50.00"), Currency.USD))
        )

        auditor = AuditReconciliationUseCase(account_repo, ledger_repo)
        report = auditor.reconcile()

        assert report.is_balanced is True
        assert report.is_system_net_zero is True
        assert report.total_system_net == Decimal("0.00")
        assert len(report.discrepancies) == 0
        assert report.accounts_audited_count == 2
        assert report.ledger_entries_count == 4

    def test_reconcile_catches_unbalanced_ledger_entry(self) -> None:
        """Injecting an unbalanced rogue ledger entry causes reconciliation to fail."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("100.00"), Currency.USD)))
        # Rogue entry with non-zero amount
        ledger_repo.append_entry(
            LedgerEntry.create_credit(
                entry_id="ROGUE-1",
                transaction_id="TX-ROGUE",
                account_id="ACC-1",
                amount=Decimal("5.00"),
                balance_after=Decimal("105.00"),
            )
        )

        auditor = AuditReconciliationUseCase(account_repo, ledger_repo)
        report = auditor.reconcile()

        assert report.is_balanced is False
        assert report.is_system_net_zero is False
        assert report.total_system_net == Decimal("5.00")

        # Must raise when raise_on_error is True
        with pytest.raises(ReconciliationDivergenceError) as exc_info:
            auditor.reconcile(raise_on_error=True)
        assert exc_info.value.divergence == Decimal("5.00")

    def test_reconcile_catches_tampered_account_balance(self) -> None:
        """Directly altering an account balance without a ledger entry is detected."""
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-2", "Bob", Money(Decimal("50.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)
        use_case.execute(
            TransferCommand("ACC-1", "ACC-2", Money(Decimal("100.00"), Currency.USD))
        )

        # Tamper: magically inflate Alice's balance by $50 directly in database
        alice = account_repo.get_by_id("ACC-1")
        assert alice is not None
        alice.balance = Money(Decimal("450.00"), Currency.USD)
        account_repo.save(alice)

        auditor = AuditReconciliationUseCase(account_repo, ledger_repo)
        report = auditor.reconcile()

        assert report.is_balanced is False
        assert len(report.discrepancies) == 1
        disc = report.discrepancies[0]
        assert disc.account_id == "ACC-1"
        assert disc.actual_balance == Decimal("450.00")
        assert disc.expected_balance == Decimal("400.00")
        assert disc.discrepancy == Decimal("50.00")


class TestQueryBalanceUseCase:
    """Verifies fast read path with Cache-Aside semantics."""

    def test_query_balance_cache_miss_and_hit(self) -> None:
        """First call queries repository (miss), second call reads L1 cache (hit)."""
        account_repo = InMemoryAccountRepository()
        cache = LRUTTLCache(capacity=10)

        account_repo.save(Account("ACC-1", "Alice", Money(Decimal("250.75"), Currency.USD)))

        query_use_case = QueryBalanceUseCase(account_repo, cache=cache, ttl_seconds=60)

        # First call: Cache miss
        dto1 = query_use_case.execute("ACC-1")
        assert dto1.account_id == "ACC-1"
        assert dto1.balance == Decimal("250.75")
        assert dto1.cached is False

        # Second call: Cache hit
        dto2 = query_use_case.execute("ACC-1")
        assert dto2.account_id == "ACC-1"
        assert dto2.balance == Decimal("250.75")
        assert dto2.cached is True

    def test_query_balance_nonexistent_account_raises(self) -> None:
        """Querying an unknown account ID raises AccountNotFoundError."""
        account_repo = InMemoryAccountRepository()
        query_use_case = QueryBalanceUseCase(account_repo)

        with pytest.raises(AccountNotFoundError) as exc_info:
            query_use_case.execute("ACC-UNKNOWN")
        assert exc_info.value.account_id == "ACC-UNKNOWN"
