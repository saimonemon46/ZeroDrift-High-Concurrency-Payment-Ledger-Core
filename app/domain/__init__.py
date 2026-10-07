"""Domain Layer for LedgerCore.

Contains enterprise financial domain logic, entities, value objects, state machines,
and strategies with zero external dependencies.
"""

from app.domain.exceptions import (
    AccountFrozenError,
    AccountInactiveError,
    AccountStatusError,
    CurrencyMismatchError,
    DomainError,
    IllegalStateTransitionError,
    InsufficientFundsError,
    InvalidMoneyError,
    InvalidStateTransitionError,
    LedgerInvariantViolationError,
)
from app.domain.models import (
    Account,
    AccountStatus,
    EntryType,
    LedgerEntry,
    Transaction,
)
from app.domain.state_machine import (
    CommittedState,
    FailedState,
    InitiatedState,
    ITransactionState,
    LockedState,
    ReversedState,
    SettledState,
    TransactionState,
    TransactionStateMachine,
)
from app.domain.strategies import (
    FlatFeeStrategy,
    IFeeStrategy,
    MerchantFeeStrategy,
    P2PFeeStrategy,
    P2PPeerFeeStrategy,
    TieredCashOutStrategy,
)
from app.domain.value_objects import (
    AccountId,
    Currency,
    EntryId,
    Money,
    TransactionId,
)

__all__ = [
    # Value Objects
    "Money",
    "Currency",
    "AccountId",
    "TransactionId",
    "EntryId",
    # Entities
    "Account",
    "AccountStatus",
    "LedgerEntry",
    "EntryType",
    "Transaction",
    # State Machine
    "TransactionState",
    "TransactionStateMachine",
    "ITransactionState",
    "InitiatedState",
    "LockedState",
    "CommittedState",
    "SettledState",
    "FailedState",
    "ReversedState",
    # Strategies
    "IFeeStrategy",
    "P2PPeerFeeStrategy",
    "P2PFeeStrategy",
    "MerchantFeeStrategy",
    "TieredCashOutStrategy",
    "FlatFeeStrategy",
    # Exceptions
    "DomainError",
    "InsufficientFundsError",
    "CurrencyMismatchError",
    "InvalidMoneyError",
    "AccountStatusError",
    "AccountInactiveError",
    "AccountFrozenError",
    "InvalidStateTransitionError",
    "IllegalStateTransitionError",
    "LedgerInvariantViolationError",
]
