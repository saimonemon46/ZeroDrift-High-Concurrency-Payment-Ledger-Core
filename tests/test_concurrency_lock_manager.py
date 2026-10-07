"""Verification test suite for CanonicalLockManager and Deadlock Elimination."""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.domain.models import Account
from app.domain.value_objects import Money
from app.infrastructure.concurrency import CanonicalLockManager


class TestCanonicalLockManager:
    """Verifies deterministic lock ordering and Coffman circular-wait elimination."""

    def test_canonical_ordering_independent_of_argument_sequence(self) -> None:
        """Regardless of input order, locks are acquired in min(ID) -> max(ID) order."""
        lock_mgr = CanonicalLockManager()
        acc_101 = Account("ACC-101", "User 101", Money(Decimal("100.00"), "BDT"))
        acc_202 = Account("ACC-202", "User 202", Money(Decimal("100.00"), "BDT"))

        # Test A then B
        with lock_mgr.acquire_pair(acc_101, acc_202) as (first, second):
            assert first.account_id == "ACC-101"
            assert second.account_id == "ACC-202"

        # Test B then A (inverted argument order)
        with lock_mgr.acquire_pair(acc_202, acc_101) as (first, second):
            assert first.account_id == "ACC-202"
            assert second.account_id == "ACC-101"

    def test_rejects_identical_account_pair(self) -> None:
        """Attempting to lock the exact same account as a pair raises ValueError."""
        lock_mgr = CanonicalLockManager()
        acc_101 = Account("ACC-101", "User 101", Money(Decimal("100.00"), "BDT"))

        with pytest.raises(ValueError, match="identical account ID"):
            with lock_mgr.acquire_pair(acc_101, acc_101):
                pass

    def test_acquire_many_arbitrary_accounts_sorted(self) -> None:
        """Acquires an arbitrary list of accounts in monotonically sorted order."""
        lock_mgr = CanonicalLockManager()
        accounts = [
            Account("ACC-300", "U3", Money(Decimal("10.00"), "BDT")),
            Account("ACC-100", "U1", Money(Decimal("10.00"), "BDT")),
            Account("ACC-200", "U2", Money(Decimal("10.00"), "BDT")),
        ]

        with lock_mgr.acquire_many(accounts) as locked_list:
            assert len(locked_list) == 3

    def test_acquire_many_rejects_duplicates(self) -> None:
        """Duplicate accounts in collection raise ValueError."""
        lock_mgr = CanonicalLockManager()
        acc_1 = Account("ACC-1", "U1", Money(Decimal("10.00"), "BDT"))
        acc_dup = Account("ACC-1", "U1-Clone", Money(Decimal("10.00"), "BDT"))

        with pytest.raises(ValueError, match="duplicate account IDs"):
            with lock_mgr.acquire_many([acc_1, acc_dup]):
                pass

    def test_simultaneous_bilateral_transfers_no_deadlock(self) -> None:
        """Stress tests simultaneous cross-transfers (A -> B and B -> A) across threads.

        Without canonical ordering, this scenario deadlocks immediately under Coffman circular wait.
        """
        lock_mgr = CanonicalLockManager()
        acc_a = Account("ACC-AAA", "Alice", Money(Decimal("500.00"), "BDT"))
        acc_b = Account("ACC-BBB", "Bob", Money(Decimal("500.00"), "BDT"))

        transfer_count = 200
        completed = 0

        def transfer_a_to_b() -> None:
            nonlocal completed
            for _ in range(transfer_count):
                with lock_mgr.acquire_pair(acc_a, acc_b):
                    acc_a.debit(Money(Decimal("1.00"), "BDT"))
                    acc_b.credit(Money(Decimal("1.00"), "BDT"))
                    completed += 1
                time.sleep(0.0001)

        def transfer_b_to_a() -> None:
            nonlocal completed
            for _ in range(transfer_count):
                with lock_mgr.acquire_pair(acc_b, acc_a):  # Inverted argument order
                    acc_b.debit(Money(Decimal("1.00"), "BDT"))
                    acc_a.credit(Money(Decimal("1.00"), "BDT"))
                    completed += 1
                time.sleep(0.0001)

        with ThreadPoolExecutor(max_workers=8) as executor:
            f1 = executor.submit(transfer_a_to_b)
            f2 = executor.submit(transfer_b_to_a)
            f1.result(timeout=10.0)
            f2.result(timeout=10.0)

        assert completed == transfer_count * 2
        # Final combined balance must remain strictly conserved
        total_balance = acc_a.balance.amount + acc_b.balance.amount
        assert total_balance == Decimal("1000.00")
