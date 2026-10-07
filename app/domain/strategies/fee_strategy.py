"""Pluggable fee calculation strategies adhering to the GoF Strategy Pattern.

This module encapsulates diverse commercial and interchange fee policies into
isolated strategy classes, allowing runtime interchangeability without modifying
the core ledger transfer engine (Open/Closed Principle).
"""

from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal

from app.domain.exceptions import CurrencyMismatchError
from app.domain.value_objects import Money


class IFeeStrategy(ABC):
    """Abstract Strategy interface defining the contract for transaction fee calculation."""

    @abstractmethod
    def calculate_fee(self, amount: Money) -> Money:
        """Calculates the applicable commercial transaction fee for a given transfer amount.

        Args:
            amount: The principle monetary transfer amount.

        Returns:
            Money: The computed fee in the identical currency.
        """
        pass


class P2PPeerFeeStrategy(IFeeStrategy):
    """Peer-to-Peer transfer fee strategy.

    Default policy is zero fee ($0.00). Optionally supports flat fees or percentages with a cap.
    """

    def __init__(
        self,
        flat_fee: Decimal = Decimal("0.00"),
        rate: Decimal = Decimal("0.00"),
        max_fee: Money | None = None,
    ) -> None:
        self.flat_fee = flat_fee
        self.rate = rate
        self.max_fee = max_fee

    def calculate_fee(self, amount: Money) -> Money:
        if self.rate == Decimal("0.00") and self.flat_fee == Decimal("0.00"):
            return Money(Decimal("0.00"), amount.currency)

        calculated = (amount.amount * self.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_fee_amt = calculated + self.flat_fee

        if self.max_fee is not None:
            if amount.currency != self.max_fee.currency:
                raise CurrencyMismatchError("Fee cap currency mismatch.")
            if total_fee_amt > self.max_fee.amount:
                total_fee_amt = self.max_fee.amount

        return Money(total_fee_amt, amount.currency)


# Alias P2PFeeStrategy for P2PPeerFeeStrategy
P2PFeeStrategy = P2PPeerFeeStrategy


class MerchantFeeStrategy(IFeeStrategy):
    """Commercial merchant payment fee strategy.

    Applies a percentage rate (default 1.5%) on the transfer amount with optional
    minimum fee floor and maximum fee ceiling.
    """

    def __init__(
        self,
        rate: Decimal = Decimal("0.015"),
        minimum_fee: Money | None = None,
        max_fee: Money | None = None,
    ) -> None:
        self.rate = rate
        self.minimum_fee = minimum_fee
        self.max_fee = max_fee

    def calculate_fee(self, amount: Money) -> Money:
        raw_fee = (amount.amount * self.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if self.minimum_fee is not None:
            if amount.currency != self.minimum_fee.currency:
                raise CurrencyMismatchError("Minimum fee currency mismatch.")
            if raw_fee < self.minimum_fee.amount:
                raw_fee = self.minimum_fee.amount

        if self.max_fee is not None:
            if amount.currency != self.max_fee.currency:
                raise CurrencyMismatchError("Maximum fee currency mismatch.")
            if raw_fee > self.max_fee.amount:
                raw_fee = self.max_fee.amount

        return Money(raw_fee, amount.currency)


class TieredCashOutStrategy(IFeeStrategy):
    """Tiered Cash-Out fee strategy (ATM / Agent cash-out).

    Applies a tiered flat fee:
    - 15.00 BDT for transfers <= 5,000.00 BDT
    - 25.00 BDT for transfers > 5,000.00 BDT
    """

    def __init__(
        self,
        threshold: Decimal = Decimal("5000.00"),
        tier1_fee: Decimal = Decimal("15.00"),
        tier2_fee: Decimal = Decimal("25.00"),
    ) -> None:
        self.threshold = threshold
        self.tier1_fee = tier1_fee
        self.tier2_fee = tier2_fee

    def calculate_fee(self, amount: Money) -> Money:
        fee_amt = self.tier1_fee if amount.amount <= self.threshold else self.tier2_fee
        return Money(fee_amt.quantize(Decimal("0.01")), amount.currency)


class FlatFeeStrategy(IFeeStrategy):
    """Fixed flat fee strategy regardless of transaction size."""

    def __init__(self, flat_fee: Decimal = Decimal("5.00")) -> None:
        self.flat_fee = flat_fee.quantize(Decimal("0.01"))

    def calculate_fee(self, amount: Money) -> Money:
        return Money(self.flat_fee, amount.currency)
