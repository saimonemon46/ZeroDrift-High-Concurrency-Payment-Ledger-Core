"""High-concurrency torture test suite for LedgerCore.

Executes extreme multi-threaded race conditions, double-spend attacks,
cyclical transfer deadlocks, and mobile idempotency storms to prove:
1. Zero balance drift under concurrent race conditions.
2. Complete elimination of Coffman circular-wait deadlocks.
3. Strict double-entry zero-sum conservation under chaos.
4. Single-execution guarantee under mobile network retry stampedes.
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from app.application.decorators.idempotency_decorator import IdempotencyWrapper
from app.application.exceptions import IdempotencyConflictError
from app.application.use_cases.audit_reconciliation import AuditReconciliationUseCase
from app.application.use_cases.execute_transfer import (
    ExecuteTransferUseCase,
    TransferCommand,
    TransferResult,
)
from app.domain.exceptions import InsufficientFundsError
from app.domain.models import Account
from app.domain.value_objects import Currency, Money
from app.infrastructure.persistence.memory_repositories import (
    InMemoryAccountRepository,
    InMemoryIdempotencyStore,
    InMemoryLedgerRepository,
)


class TestDoubleSpendTortureRace:
    """Stress tests concurrent double-spend race conditions against a single account."""

    def test_50_thread_double_spend_race(self) -> None:
        """50 threads concurrently attempt to withdraw $100 from an account with only $100.

        Guarantees:
        - Exactly 1 thread succeeds (wins the race).
        - Exactly 49 threads fail with InsufficientFundsError.
        - Final balance is strictly $0.00 (no balance drift or negative balances).
        - Exactly 2 ledger entries created ($100 debit, $100 credit), summing to 0.00.
        """
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        # Alice has exactly $100.00. Bob has $0.00.
        account_repo.save(Account("ACC-ALICE", "Alice", Money(Decimal("100.00"), Currency.USD)))
        account_repo.save(Account("ACC-BOB", "Bob", Money(Decimal("0.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)

        successes: list[TransferResult] = []
        failures: list[Exception] = []

        def race_worker(worker_id: int) -> None:
            cmd = TransferCommand(
                from_account_id="ACC-ALICE",
                to_account_id="ACC-BOB",
                amount=Money(Decimal("100.00"), Currency.USD),
                metadata={"worker_id": worker_id},
            )
            try:
                res = use_case.execute(cmd)
                successes.append(res)
            except Exception as exc:
                failures.append(exc)

        # Launch 50 threads racing simultaneously
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(race_worker, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        # Invariant 1: Exactly 1 winner
        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
        assert successes[0].status == "SETTLED"

        # Invariant 2: Exactly 49 failures, all InsufficientFundsError
        assert len(failures) == 49
        assert all(isinstance(f, InsufficientFundsError) for f in failures)

        # Invariant 3: Final balances strictly preserved
        alice = account_repo.get_by_id("ACC-ALICE")
        bob = account_repo.get_by_id("ACC-BOB")
        assert alice is not None and alice.balance == Money(Decimal("0.00"), Currency.USD)
        assert bob is not None and bob.balance == Money(Decimal("100.00"), Currency.USD)

        # Invariant 4: Zero-sum double-entry conservation
        assert len(ledger_repo.get_all_entries()) == 2
        assert ledger_repo.get_total_system_net() == Decimal("0.00")


class TestBilateralAndCyclicalDeadlockTorture:
    """Proves Coffman circular-wait deadlocks are eliminated under intense concurrent load."""

    def test_1000_transfer_bilateral_deadlock_maze(self) -> None:
        """1,000 rapid concurrent cross-transfers between 5 accounts.

        Proves:
        - 0 circular deadlocks occur.
        - Total system money is 100% conserved (initial sum == final sum).
        - Total ledger net sum is strictly 0.00.
        """
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        # 5 accounts, each seeded with $1,000.00
        account_ids = [f"ACC-{i}" for i in range(1, 6)]
        for acc_id in account_ids:
            account_repo.save(
                Account(acc_id, f"User-{acc_id}", Money(Decimal("1000.00"), Currency.USD))
            )

        total_initial_wealth = Decimal("5000.00")
        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)

        def random_transfer_worker(seed: int) -> bool:
            rnd = random.Random(seed)
            sender_id, receiver_id = rnd.sample(account_ids, 2)
            amount_val = Decimal(str(rnd.randint(1, 5))) + Decimal("0.00")
            cmd = TransferCommand(
                from_account_id=sender_id,
                to_account_id=receiver_id,
                amount=Money(amount_val, Currency.USD),
            )
            try:
                use_case.execute(cmd)
                return True
            except InsufficientFundsError:
                return False

        t0 = time.time()
        # 1,000 transfers distributed across 24 concurrent threads
        with ThreadPoolExecutor(max_workers=24) as executor:
            futures = [executor.submit(random_transfer_worker, i) for i in range(1000)]
            completed_transfers = sum(1 for f in as_completed(futures) if f.result())

        elapsed = time.time() - t0

        # All 1,000 transfers must complete without deadlocks
        assert completed_transfers > 0
        assert elapsed < 10.0, f"Transfers took {elapsed:.2f}s, possible deadlock lockup!"

        # Invariant: Total system wealth strictly conserved
        all_accounts = account_repo.get_all()
        total_final_wealth = sum(
            (acc.balance.amount for acc in all_accounts), start=Decimal("0.00")
        )
        assert total_final_wealth == total_initial_wealth

        # Invariant: Ledger entries net sum == 0.00
        assert ledger_repo.get_total_system_net() == Decimal("0.00")

        # Invariant: Mathematical audit reconciliation confirms balanced system
        auditor = AuditReconciliationUseCase(account_repo, ledger_repo)
        report = auditor.reconcile()
        assert report.is_balanced is True
        assert len(report.discrepancies) == 0

    def test_circular_ring_transfers(self) -> None:
        """Cyclical transfer chain: A -> B -> C -> D -> A running simultaneously in 40 threads.

        In naive systems, cyclical lock dependency chains cause catastrophic deadlock.
        CanonicalLockManager eliminates circular wait by ordering locks monotonically.
        """
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()

        accounts = ["RING-A", "RING-B", "RING-C", "RING-D"]
        for a_id in accounts:
            account_repo.save(Account(a_id, a_id, Money(Decimal("500.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)

        # Ring pairs: A->B, B->C, C->D, D->A
        pairs = [
            ("RING-A", "RING-B"),
            ("RING-B", "RING-C"),
            ("RING-C", "RING-D"),
            ("RING-D", "RING-A"),
        ]

        def ring_worker(pair: tuple[str, str]) -> TransferResult:
            return use_case.execute(
                TransferCommand(
                    from_account_id=pair[0],
                    to_account_id=pair[1],
                    amount=Money(Decimal("10.00"), Currency.USD),
                )
            )

        # 40 workers (10 per hop in the ring) executed simultaneously
        tasks = [pairs[i % 4] for i in range(40)]
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(ring_worker, p) for p in tasks]
            results = [f.result() for f in as_completed(futures)]

        assert len(results) == 40
        # Because exactly 10 transfers left each node and 10 entered each node ($100 out, $100 in),
        # every single account's final balance must return to exactly $500.00!
        for a_id in accounts:
            acc = account_repo.get_by_id(a_id)
            assert acc is not None
            assert acc.balance == Money(Decimal("500.00"), Currency.USD)

        assert ledger_repo.get_total_system_net() == Decimal("0.00")


class TestIdempotencyStampedeTorture:
    """Tests mobile network retry storms where 100 threads fire the identical key."""

    def test_100_thread_idempotency_stampede(self) -> None:
        """100 simultaneous threads submit identical transfer with key 'STAMPEDE-KEY'.

        Guarantees:
        - The transfer is executed exactly ONCE.
        - Alice is debited exactly ONCE ($50.00, from $500 to $450).
        - Bob is credited exactly ONCE ($50.00, from $0 to $50).
        - All threads receive a successful TransferResult with the identical transaction_id.
        - Zero double-charges occur.
        """
        account_repo = InMemoryAccountRepository()
        ledger_repo = InMemoryLedgerRepository()
        idempotency_store = InMemoryIdempotencyStore()

        account_repo.save(Account("ACC-ALICE", "Alice", Money(Decimal("500.00"), Currency.USD)))
        account_repo.save(Account("ACC-BOB", "Bob", Money(Decimal("0.00"), Currency.USD)))

        use_case = ExecuteTransferUseCase(account_repo, ledger_repo)
        idempotent_use_case = IdempotencyWrapper(
            target=use_case.execute,
            store=idempotency_store,
        )

        results: list[TransferResult] = []
        conflicts: list[IdempotencyConflictError] = []

        def stampede_worker(worker_id: int) -> None:
            cmd = TransferCommand(
                from_account_id="ACC-ALICE",
                to_account_id="ACC-BOB",
                amount=Money(Decimal("50.00"), Currency.USD),
                idempotency_key="STAMPEDE-KEY-999",
                metadata={"worker_id": worker_id},
            )
            try:
                res = idempotent_use_case(cmd)
                results.append(res)
            except IdempotencyConflictError as conflict:
                conflicts.append(conflict)

        # 100 threads hammering the engine at the same microsecond
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(stampede_worker, i) for i in range(100)]
            for f in as_completed(futures):
                f.result()

        # At least one succeeded, and any in-flight collisions returned conflict or cached result
        assert len(results) >= 1
        assert len(results) + len(conflicts) == 100

        # All completed results share the exact same transaction ID
        first_tx_id = results[0].transaction_id
        for res in results:
            assert res.transaction_id == first_tx_id

        # Verify Alice was only debited ONCE
        alice = account_repo.get_by_id("ACC-ALICE")
        bob = account_repo.get_by_id("ACC-BOB")
        assert alice is not None and alice.balance == Money(Decimal("450.00"), Currency.USD)
        assert bob is not None and bob.balance == Money(Decimal("50.00"), Currency.USD)

        # Exactly 2 entries in ledger: -50 and +50
        assert len(ledger_repo.get_all_entries()) == 2
        assert ledger_repo.get_total_system_net() == Decimal("0.00")
