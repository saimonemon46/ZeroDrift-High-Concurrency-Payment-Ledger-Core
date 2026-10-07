"""Domain entities and double-entry ledger models for LedgerCore.

This module provides the core financial entities: Account, LedgerEntry, and Transaction.
All balance mutations, double-entry bookkeeping rules, and mathematical invariants
are encapsulated within these models with zero external framework dependencies.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from app.domain.exceptions import (
    AccountFrozenError,
    AccountInactiveError,
    AccountStatusError,
    CurrencyMismatchError,
    InsufficientFundsError,
)
from app.domain.state_machine import TransactionState, TransactionStateMachine
from app.domain.value_objects import Money


class AccountStatus(StrEnum):
    """Lifecycle status of a ledger account."""
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class EntryType(StrEnum):
    """Bookkeeping double-entry classification."""
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


@dataclass
class Account:
    """Financial account entity encapsulating balance integrity and row-level locking.

    Attributes:
        account_id: Unique continuous identifier for the account.
        owner_name: Name of the account holder or organization.
        balance: Current settled balance represented as an immutable Money object.
        status: Current operational status (ACTIVE, FROZEN, CLOSED).
        lock: Reentrant mutex for thread-safe in-memory operations.
    """

    account_id: str
    owner_name: str
    balance: Money
    status: AccountStatus | str = AccountStatus.ACTIVE
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.balance, Money):
            raise TypeError(
                f"balance must be a Money instance, got {type(self.balance).__name__}"
            )

        if isinstance(self.status, str) and not isinstance(self.status, AccountStatus):
            try:
                self.status = AccountStatus(self.status.upper())
            except ValueError as err:
                raise AccountStatusError(f"Invalid account status: '{self.status}'.") from err

    def has_sufficient_balance(self, amount: Money) -> bool:
        """Determines whether the account possesses sufficient balance for a debit."""
        if not isinstance(amount, Money):
            raise TypeError(f"amount must be a Money instance, got {type(amount).__name__}")
        if self.balance.currency != amount.currency:
            msg = (
                f"Currency mismatch: Account ({self.balance.currency}) "
                f"vs Debit ({amount.currency})."
            )
            raise CurrencyMismatchError(msg)
        return self.balance.amount >= amount.amount

    def debit(self, amount: Money) -> None:
        """Debits funds from the account, enforcing solvency and status invariants."""
        if self.status != AccountStatus.ACTIVE:
            if self.status == AccountStatus.FROZEN:
                raise AccountFrozenError(
                    f"Account {self.account_id} is FROZEN and cannot be debited."
                )
            raise AccountInactiveError(
                f"Account {self.account_id} is {self.status} and cannot be debited."
            )

        if not isinstance(amount, Money):
            raise TypeError(f"amount must be a Money instance, got {type(amount).__name__}")

        if not amount.is_positive():
            raise ValueError("Debit amount must be strictly positive.")

        if not self.has_sufficient_balance(amount):
            raise InsufficientFundsError(
                f"Account {self.account_id} balance {self.balance} < {amount}"
            )

        self.balance = self.balance.subtract(amount)

    def credit(self, amount: Money) -> None:
        """Credits funds to the account, enforcing status and currency invariants."""
        if self.status != AccountStatus.ACTIVE:
            if self.status == AccountStatus.FROZEN:
                raise AccountFrozenError(
                    f"Account {self.account_id} is FROZEN and cannot be credited."
                )
            raise AccountInactiveError(
                f"Account {self.account_id} is {self.status} and cannot be credited."
            )

        if not isinstance(amount, Money):
            raise TypeError(f"amount must be a Money instance, got {type(amount).__name__}")

        if not amount.is_positive():
            raise ValueError("Credit amount must be strictly positive.")

        if self.balance.currency != amount.currency:
            msg = (
                f"Currency mismatch: Account ({self.balance.currency}) "
                f"vs Credit ({amount.currency})."
            )
            raise CurrencyMismatchError(msg)

        self.balance = self.balance.add(amount)

    def freeze(self) -> None:
        """Freezes the account, preventing debits and credits."""
        self.status = AccountStatus.FROZEN

    def activate(self) -> None:
        """Activates the account for active transactions."""
        self.status = AccountStatus.ACTIVE

    def close(self) -> None:
        """Closes the account permanently."""
        self.status = AccountStatus.CLOSED

    def is_active(self) -> bool:
        """Returns True if the account is in ACTIVE status."""
        return self.status == AccountStatus.ACTIVE

    def is_frozen(self) -> bool:
        """Returns True if the account is in FROZEN status."""
        return self.status == AccountStatus.FROZEN

    def is_closed(self) -> bool:
        """Returns True if the account is in CLOSED status."""
        return self.status == AccountStatus.CLOSED


@dataclass(frozen=True)
class LedgerEntry:
    """Immutable, append-only double-entry bookkeeping journal record.

    Attributes:
        entry_id: Globally unique record identifier.
        transaction_id: Identifier of the causal transaction.
        account_id: Account affected by this entry.
        amount: Signed Decimal amount (Negative for DEBIT, Positive for CREDIT).
        balance_after: Account balance snapshot immediately after entry was posted.
        entry_type: DEBIT or CREDIT classification.
        timestamp: Epoch timestamp in seconds.
        created_at: UTC timestamp datetime.
    """

    entry_id: str
    transaction_id: str
    account_id: str
    amount: Decimal
    balance_after: Decimal
    entry_type: EntryType | str
    timestamp: float = field(default_factory=time.time)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("LedgerEntry amount must be a Decimal instance.")

        if not isinstance(self.balance_after, Decimal):
            raise TypeError("LedgerEntry balance_after must be a Decimal instance.")

        exponent = self.amount.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -2:
            raise ValueError("LedgerEntry amount precision cannot exceed 2 decimal places.")

        bal_exponent = self.balance_after.as_tuple().exponent
        if isinstance(bal_exponent, int) and bal_exponent < -2:
            raise ValueError("LedgerEntry balance_after precision cannot exceed 2 decimal places.")

        if isinstance(self.entry_type, str) and not isinstance(self.entry_type, EntryType):
            try:
                object.__setattr__(self, "entry_type", EntryType(self.entry_type.upper()))
            except ValueError as err:
                raise ValueError(f"Invalid entry_type: '{self.entry_type}'.") from err

        # In double-entry convention: debits are negative, credits are positive
        if self.entry_type == EntryType.DEBIT and self.amount > Decimal("0.00"):
            raise ValueError(f"Debit entry amount must be non-positive, got {self.amount}")

        if self.entry_type == EntryType.CREDIT and self.amount < Decimal("0.00"):
            raise ValueError(f"Credit entry amount must be non-negative, got {self.amount}")

    @classmethod
    def create_debit(
        cls,
        entry_id: str,
        transaction_id: str,
        account_id: str,
        amount: Decimal | Money,
        balance_after: Decimal | Money,
        timestamp: float | None = None,
    ) -> "LedgerEntry":
        """Factory method creating an immutable DEBIT ledger entry (negative signed)."""
        raw_amount = amount.amount if isinstance(amount, Money) else amount
        signed_amount = -abs(raw_amount)
        bal_after = balance_after.amount if isinstance(balance_after, Money) else balance_after
        ts = timestamp if timestamp is not None else time.time()
        return cls(
            entry_id=entry_id,
            transaction_id=transaction_id,
            account_id=account_id,
            amount=signed_amount,
            balance_after=bal_after,
            entry_type=EntryType.DEBIT,
            timestamp=ts,
        )

    @classmethod
    def create_credit(
        cls,
        entry_id: str,
        transaction_id: str,
        account_id: str,
        amount: Decimal | Money,
        balance_after: Decimal | Money,
        timestamp: float | None = None,
    ) -> "LedgerEntry":
        """Factory method creating an immutable CREDIT ledger entry (positive signed)."""
        raw_amount = amount.amount if isinstance(amount, Money) else amount
        signed_amount = abs(raw_amount)
        bal_after = balance_after.amount if isinstance(balance_after, Money) else balance_after
        ts = timestamp if timestamp is not None else time.time()
        return cls(
            entry_id=entry_id,
            transaction_id=transaction_id,
            account_id=account_id,
            amount=signed_amount,
            balance_after=bal_after,
            entry_type=EntryType.CREDIT,
            timestamp=ts,
        )


@dataclass
class Transaction:
    """Financial transaction entity representing a money movement between accounts.

    Attributes:
        transaction_id: Globally unique transaction identifier.
        from_account_id: Source account being debited.
        to_account_id: Destination account being credited.
        amount: Principle monetary amount being transferred.
        fee: Platform or intermediary commercial fee.
        state: Current lifecycle state managed by the State Pattern.
        created_at: Creation epoch timestamp.
        error_message: Optional reason recorded if the transaction failed.
    """

    transaction_id: str
    from_account_id: str
    to_account_id: str
    amount: Money
    fee: Money = field(default_factory=lambda: Money(Decimal("0.00")))
    state: TransactionState | str = TransactionState.INITIATED
    created_at: float = field(default_factory=time.time)
    error_message: str | None = None
    _state_machine: TransactionStateMachine = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Money):
            raise TypeError(f"amount must be Money, got {type(self.amount).__name__}")
        if not isinstance(self.fee, Money):
            raise TypeError(f"fee must be Money, got {type(self.fee).__name__}")
        if self.amount.currency != self.fee.currency:
            msg = (
                f"Transfer amount currency ({self.amount.currency}) "
                f"does not match fee currency ({self.fee.currency})"
            )
            raise CurrencyMismatchError(msg)

        resolved_state = (
            TransactionState(self.state)
            if isinstance(self.state, str) and not isinstance(self.state, TransactionState)
            else self.state
        )
        self.state = resolved_state
        self._state_machine = TransactionStateMachine(initial_state=resolved_state)

    def transition_to(self, target: TransactionState | str) -> None:
        """Transitions transaction state via the internal finite-state machine."""
        self._state_machine.transition_to(target)
        self.state = self._state_machine.state

    def can_transition_to(self, target: TransactionState | str) -> bool:
        """Checks if transitioning to target state is permissible."""
        return self._state_machine.can_transition_to(target)

    @property
    def total_debited_amount(self) -> Money:
        """Calculates total deduction from the sender account (amount + fee)."""
        return self.amount.add(self.fee)
