"""Domain layer exceptions for LedgerCore.

This module defines enterprise financial domain exceptions with zero external
framework dependencies. These exceptions enforce financial domain rule invariants,
balance constraints, and lifecycle state transition boundaries.
"""

from typing import Any


class DomainError(Exception):
    """Base exception for all financial domain errors."""
    pass


class InsufficientFundsError(DomainError):
    """Raised when an account balance is insufficient to satisfy a debit."""
    pass


class CurrencyMismatchError(DomainError, ValueError):
    """Raised when attempting financial operations between distinct currencies."""
    pass


class InvalidMoneyError(DomainError, ValueError):
    """Raised when a Money value object receives invalid parameters."""
    pass


class AccountStatusError(DomainError):
    """Base exception for operations rejected due to invalid account status."""
    pass


class AccountInactiveError(AccountStatusError):
    """Raised when a debit or credit is attempted on an inactive or closed account."""
    pass


class AccountFrozenError(AccountStatusError):
    """Raised when a financial operation is attempted on a frozen account."""
    pass


class InvalidStateTransitionError(DomainError):
    """Raised when an illegal transaction state machine transition is attempted."""

    def __init__(
        self,
        from_state: Any,
        to_state: Any,
        message: str | None = None,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        msg = message or f"Illegal state transition from {from_state} to {to_state}."
        super().__init__(msg)


# Alias for compatibility across architectural specifications
IllegalStateTransitionError = InvalidStateTransitionError


class LedgerInvariantViolationError(DomainError):
    """Raised when double-entry bookkeeping conservation invariants are violated."""
    pass
