"""Immutable domain value objects for LedgerCore.

This module provides financial value objects with exact Decimal representation,
guaranteeing mathematical safety, currency isolation, and zero floating-point
representation drift.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.domain.exceptions import CurrencyMismatchError, InvalidMoneyError


class Currency(StrEnum):
    """Standard ISO currency codes supported by the ledger engine."""
    BDT = "BDT"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


AccountId = str
TransactionId = str
EntryId = str


@dataclass(frozen=True)
class Money:
    """Immutable monetary representation enforcing exact Decimal scale and currency isolation.

    Attributes:
        amount: Exact Decimal monetary value with up to 2 decimal places.
        currency: ISO 4217 standard currency code (default: BDT).
    """

    amount: Decimal
    currency: str = "BDT"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money amount must be a decimal.Decimal instance.")

        if not self.amount.is_finite():
            raise InvalidMoneyError("Money amount must be a finite Decimal number.")

        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("Currency must be a non-empty string.")

        exponent = self.amount.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -2:
            raise ValueError("Money precision cannot exceed 2 decimal places.")

        # Normalize to canonical 2-decimal scale and normalized currency code
        canonical_amount = self.amount.quantize(Decimal("0.01"))
        object.__setattr__(self, "amount", canonical_amount)
        object.__setattr__(self, "currency", self.currency.strip().upper())

    def _assert_same_currency(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Expected Money instance, got {type(other).__name__}")
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Currency mismatch: {self.currency} vs {other.currency}"
            )

    def add(self, other: "Money") -> "Money":
        """Adds two Money amounts of identical currency."""
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def subtract(self, other: "Money") -> "Money":
        """Subtracts another Money amount of identical currency."""
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def multiply(self, factor: Decimal | int) -> "Money":
        """Multiplies Money amount by a scalar Decimal or integer factor."""
        if not isinstance(factor, (Decimal, int)):
            raise TypeError("Multiply factor must be a Decimal or int instance.")
        dec_factor = Decimal(factor) if isinstance(factor, int) else factor
        product = (self.amount * dec_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return Money(product, self.currency)

    def is_positive(self) -> bool:
        """Returns True if the amount is strictly greater than 0.00."""
        return self.amount > Decimal("0.00")

    def is_zero(self) -> bool:
        """Returns True if the amount is exactly 0.00."""
        return self.amount == Decimal("0.00")

    def is_negative(self) -> bool:
        """Returns True if the amount is strictly less than 0.00."""
        return self.amount < Decimal("0.00")

    def __add__(self, other: "Money") -> "Money":
        return self.add(other)

    def __sub__(self, other: "Money") -> "Money":
        return self.subtract(other)

    def __mul__(self, other: Decimal | int) -> "Money":
        return self.multiply(other)

    def __rmul__(self, other: Decimal | int) -> "Money":
        return self.multiply(other)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __abs__(self) -> "Money":
        return Money(abs(self.amount), self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return False
        return self.currency == other.currency and self.amount == other.amount

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency}"

    def __repr__(self) -> str:
        return f"Money(amount=Decimal('{self.amount:.2f}'), currency='{self.currency}')"

    @classmethod
    def zero(cls, currency: str = "BDT") -> "Money":
        """Factory method returning 0.00 Money in the specified currency."""
        return cls(Decimal("0.00"), currency)

    @classmethod
    def from_str(cls, amount_str: str, currency: str = "BDT") -> "Money":
        """Factory method creating Money from a string decimal representation."""
        return cls(Decimal(amount_str), currency)
