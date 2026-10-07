"""Canonical Lock Manager eliminating deadlocks via deterministic resource ordering.

Proves Coffman circular-wait prevention:
For any simultaneous bilateral transfers (A -> B and B -> A), locks are always
acquired in ascending lexicographical sequence (min(A, B) -> max(A, B)).
"""

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager

from app.domain.models import Account


class CanonicalLockManager:
    """Manages thread-safe acquisition of account locks in globally sorted order.

    Eliminates circular-wait deadlocks by guaranteeing monotonic lock acquisition:
    forall A, B : acquire(min(A.id, B.id)) before acquire(max(A.id, B.id)).
    """

    @contextmanager
    def acquire_pair(
        self,
        acc_a: Account,
        acc_b: Account,
    ) -> Iterator[tuple[Account, Account]]:
        """Acquires reentrant locks for two accounts in deterministic lexicographical order.

        Args:
            acc_a: First account in transaction.
            acc_b: Second account in transaction.

        Yields:
            tuple[Account, Account]: The original pair (acc_a, acc_b) while holding both locks.

        Raises:
            ValueError: If attempting to lock identical account IDs.
        """
        if acc_a.account_id == acc_b.account_id:
            raise ValueError(
                f"Cannot acquire pair for identical account ID: '{acc_a.account_id}'."
            )

        # Canonical sorting: min account_id always acquired before max account_id
        first, second = (
            (acc_a, acc_b)
            if acc_a.account_id < acc_b.account_id
            else (acc_b, acc_a)
        )

        with first.lock:
            with second.lock:
                yield (acc_a, acc_b)

    @contextmanager
    def acquire_many(
        self,
        accounts: Sequence[Account],
    ) -> Iterator[list[Account]]:
        """Acquires locks for an arbitrary collection of accounts in strictly sorted order.

        Args:
            accounts: Sequence of Account instances.

        Yields:
            list[Account]: The accounts while holding all respective locks.

        Raises:
            ValueError: If duplicate account IDs are present in the collection.
        """
        account_ids = [acc.account_id for acc in accounts]
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("Cannot acquire locks for duplicate account IDs in collection.")

        # Sort accounts monotonically by account_id
        sorted_accounts = sorted(accounts, key=lambda a: a.account_id)

        with ExitStack() as stack:
            for acc in sorted_accounts:
                stack.enter_context(acc.lock)
            yield list(accounts)
