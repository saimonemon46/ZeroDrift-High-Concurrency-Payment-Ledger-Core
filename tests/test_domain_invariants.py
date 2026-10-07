"""Comprehensive unit verification suite for Phase 1: Pure Domain Layer & GoF Design Patterns.

Tests all financial invariants:
1. Exact Decimal representation and floating-point bug elimination.
2. Fractional cent scale enforcement (<= 2 decimal places).
3. Currency isolation across operations.
4. Account solvency, overdraft prevention, and status invariants.
5. Double-entry bookkeeping zero-sum conservation.
6. Transaction finite-state machine lifecycle & GoF State Pattern transitions.
7. Pluggable commercial fee calculation strategies (GoF Strategy Pattern).
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.domain.exceptions import (
    AccountFrozenError,
    AccountInactiveError,
    CurrencyMismatchError,
    IllegalStateTransitionError,
    InsufficientFundsError,
    InvalidMoneyError,
    InvalidStateTransitionError,
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
    InitiatedState,
    LockedState,
    ReversedState,
    SettledState,
    TransactionState,
    TransactionStateMachine,
)
from app.domain.strategies import (
    FlatFeeStrategy,
    MerchantFeeStrategy,
    P2PFeeStrategy,
    P2PPeerFeeStrategy,
    TieredCashOutStrategy,
)
from app.domain.value_objects import Money

# ============================================================================
# 1. Money Value Object Invariants
# ============================================================================

class TestMoneyValueObject:
    """Verifies precision, scale constraints, currency isolation, and arithmetic safety."""

    def test_exact_decimal_arithmetic_eliminates_float_drift(self) -> None:
        """In IEEE 754 binary floating-point, 0.1 + 0.2 != 0.3. Money must be exact."""
        m1 = Money(Decimal("0.10"), "BDT")
        m2 = Money(Decimal("0.20"), "BDT")
        expected = Money(Decimal("0.30"), "BDT")

        result = m1.add(m2)
        assert result == expected
        assert result.amount == Decimal("0.30")
        assert (m1 + m2) == expected

    def test_normalizes_amounts_with_fewer_than_two_decimal_places(self) -> None:
        """Integral or 1-decimal Decimals should be normalized to canonical 2 decimals."""
        m_int = Money(Decimal("100"), "BDT")
        assert m_int.amount == Decimal("100.00")
        assert m_int.amount.as_tuple().exponent == -2

        m_one_dec = Money(Decimal("100.5"), "BDT")
        assert m_one_dec.amount == Decimal("100.50")
        assert m_one_dec.amount.as_tuple().exponent == -2

    def test_rejects_sub_cent_precision_exceeding_two_decimals(self) -> None:
        """Financial rules strictly prohibit sub-cent precision (> 2 decimal places)."""
        with pytest.raises(ValueError, match="Money precision cannot exceed 2 decimal places"):
            Money(Decimal("10.001"), "BDT")

        with pytest.raises(ValueError, match="Money precision cannot exceed 2 decimal places"):
            Money(Decimal("0.12345"), "USD")

    def test_rejects_non_decimal_types(self) -> None:
        """Float and integer primitives are rejected to prevent precision loss."""
        with pytest.raises(TypeError, match="must be a decimal.Decimal"):
            Money(10.50, "BDT")  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="must be a decimal.Decimal"):
            Money(100, "BDT")  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="must be a decimal.Decimal"):
            Money("100.00", "BDT")  # type: ignore[arg-type]

    def test_rejects_non_finite_decimal(self) -> None:
        """NaN and Infinities must be rejected immediately."""
        with pytest.raises(InvalidMoneyError, match="must be a finite Decimal"):
            Money(Decimal("NaN"), "BDT")

        with pytest.raises(InvalidMoneyError, match="must be a finite Decimal"):
            Money(Decimal("Infinity"), "BDT")

    def test_validates_currency_code(self) -> None:
        """Currency must be non-empty and is normalized to uppercase."""
        m = Money(Decimal("50.00"), "bdt")
        assert m.currency == "BDT"

        with pytest.raises(ValueError, match="Currency must be a non-empty string"):
            Money(Decimal("50.00"), "")

        with pytest.raises(ValueError, match="Currency must be a non-empty string"):
            Money(Decimal("50.00"), "   ")

    def test_currency_isolation_enforced_on_addition_and_subtraction(self) -> None:
        """Cross-currency operations (e.g. BDT + USD) must raise CurrencyMismatchError."""
        bdt = Money(Decimal("100.00"), "BDT")
        usd = Money(Decimal("100.00"), "USD")

        with pytest.raises(CurrencyMismatchError, match="Currency mismatch: BDT vs USD"):
            bdt.add(usd)

        with pytest.raises(CurrencyMismatchError, match="Currency mismatch: BDT vs USD"):
            bdt + usd

        with pytest.raises(CurrencyMismatchError, match="Currency mismatch: BDT vs USD"):
            bdt.subtract(usd)

        with pytest.raises(CurrencyMismatchError, match="Currency mismatch: BDT vs USD"):
            bdt - usd

        with pytest.raises(TypeError, match="Expected Money instance"):
            bdt.add("invalid")  # type: ignore[arg-type]

    def test_currency_isolation_enforced_on_comparisons(self) -> None:
        """Comparing money across different currencies must be rejected."""
        bdt = Money(Decimal("100.00"), "BDT")
        usd = Money(Decimal("100.00"), "USD")

        with pytest.raises(CurrencyMismatchError):
            _ = bdt < usd

        with pytest.raises(CurrencyMismatchError):
            _ = bdt > usd

        assert bdt != usd

    def test_money_immutability(self) -> None:
        """Money value object is frozen and cannot be mutated."""
        m = Money(Decimal("100.00"), "BDT")
        with pytest.raises(FrozenInstanceError):
            m.amount = Decimal("200.00")  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            m.currency = "USD"  # type: ignore[misc]

    def test_arithmetic_operations_and_scalar_multiplication(self) -> None:
        """Verifies addition, subtraction, scalar multiplication, and negation."""
        m = Money(Decimal("50.00"), "BDT")

        # Addition
        assert (m + Money(Decimal("25.00"), "BDT")) == Money(Decimal("75.00"), "BDT")

        # Subtraction
        assert (m - Money(Decimal("20.00"), "BDT")) == Money(Decimal("30.00"), "BDT")

        # Negation & Abs
        assert (-m) == Money(Decimal("-50.00"), "BDT")
        assert abs(-m) == Money(Decimal("50.00"), "BDT")

        # Scalar multiplication
        assert m.multiply(Decimal("2.5")) == Money(Decimal("125.00"), "BDT")
        assert (m * 2) == Money(Decimal("100.00"), "BDT")
        assert (3 * m) == Money(Decimal("150.00"), "BDT")

        # Multiplication with float must be rejected
        with pytest.raises(TypeError, match="must be a Decimal or int"):
            m.multiply(1.5)  # type: ignore[arg-type]

    def test_predicates_and_comparisons(self) -> None:
        """Tests is_positive, is_zero, is_negative, and ordering comparisons."""
        pos = Money(Decimal("10.00"), "BDT")
        zero = Money(Decimal("0.00"), "BDT")
        neg = Money(Decimal("-10.00"), "BDT")

        assert pos.is_positive() and not pos.is_zero() and not pos.is_negative()
        assert zero.is_zero() and not zero.is_positive() and not zero.is_negative()
        assert neg.is_negative() and not neg.is_positive() and not neg.is_zero()

        assert neg < zero < pos
        assert pos > zero > neg
        assert zero <= Money(Decimal("0.00"), "BDT")
        assert pos >= Money(Decimal("10.00"), "BDT")

    def test_formatting_and_factories(self) -> None:
        """Tests string representation, repr, and factory methods."""
        m = Money(Decimal("123.40"), "BDT")
        assert str(m) == "123.40 BDT"
        assert repr(m) == "Money(amount=Decimal('123.40'), currency='BDT')"

        zero = Money.zero("USD")
        assert zero == Money(Decimal("0.00"), "USD")

        parsed = Money.from_str("99.99", "BDT")
        assert parsed == Money(Decimal("99.99"), "BDT")


# ============================================================================
# 2. Account Entity Invariants
# ============================================================================

class TestAccountEntity:
    """Verifies account solvency, balance mutations, overdraft rejection, and status lifecycle."""

    def test_account_creation_and_balance_query(self) -> None:
        """Verifies account initialization with initial balance."""
        acc = Account(
            account_id="ACC-101",
            owner_name="Saimon",
            balance=Money(Decimal("500.00"), "BDT"),
        )
        assert acc.account_id == "ACC-101"
        assert acc.owner_name == "Saimon"
        assert acc.balance == Money(Decimal("500.00"), "BDT")
        assert acc.status == AccountStatus.ACTIVE
        assert acc.is_active()

    def test_has_sufficient_balance(self) -> None:
        """Evaluates balance sufficiency correctly."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))

        assert acc.has_sufficient_balance(Money(Decimal("50.00"), "BDT"))
        assert acc.has_sufficient_balance(Money(Decimal("100.00"), "BDT"))
        assert not acc.has_sufficient_balance(Money(Decimal("100.01"), "BDT"))
        assert not acc.has_sufficient_balance(Money(Decimal("200.00"), "BDT"))

        # Currency mismatch raises CurrencyMismatchError
        with pytest.raises(CurrencyMismatchError):
            acc.has_sufficient_balance(Money(Decimal("50.00"), "USD"))

    def test_credit_increases_balance_exactly(self) -> None:
        """Credit adds funds to balance with exact precision."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))
        acc.credit(Money(Decimal("50.25"), "BDT"))
        assert acc.balance == Money(Decimal("150.25"), "BDT")

    def test_credit_rejects_non_positive_amounts(self) -> None:
        """Credit amount must be strictly positive."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))

        with pytest.raises(ValueError, match="strictly positive"):
            acc.credit(Money(Decimal("0.00"), "BDT"))

        with pytest.raises(ValueError, match="strictly positive"):
            acc.credit(Money(Decimal("-10.00"), "BDT"))

    def test_credit_rejects_currency_mismatch(self) -> None:
        """Crediting cross-currency funds is rejected."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))
        with pytest.raises(CurrencyMismatchError):
            acc.credit(Money(Decimal("50.00"), "USD"))

    def test_debit_decreases_balance_correctly(self) -> None:
        """Debit reduces balance by the exact amount."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))
        acc.debit(Money(Decimal("40.00"), "BDT"))
        assert acc.balance == Money(Decimal("60.00"), "BDT")

    def test_debit_exact_balance_reaches_zero(self) -> None:
        """Debiting exact remaining balance leaves balance at 0.00."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))
        acc.debit(Money(Decimal("100.00"), "BDT"))
        assert acc.balance == Money(Decimal("0.00"), "BDT")

    def test_immediate_rejection_of_overdraft_attempts(self) -> None:
        """Overdraft attempts raise InsufficientFundsError and preserve initial balance."""
        initial_balance = Money(Decimal("100.00"), "BDT")
        acc = Account("ACC-1", "User A", initial_balance)

        with pytest.raises(InsufficientFundsError, match="balance.*<"):
            acc.debit(Money(Decimal("100.01"), "BDT"))

        # State must remain 100% untouched (atomic invariant)
        assert acc.balance == initial_balance

        with pytest.raises(InsufficientFundsError):
            acc.debit(Money(Decimal("1000.00"), "BDT"))

        assert acc.balance == initial_balance

    def test_debit_rejects_non_positive_amounts(self) -> None:
        """Debit amount must be strictly positive."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))

        with pytest.raises(ValueError, match="strictly positive"):
            acc.debit(Money(Decimal("0.00"), "BDT"))

        with pytest.raises(ValueError, match="strictly positive"):
            acc.debit(Money(Decimal("-50.00"), "BDT"))

    def test_account_status_invariants_and_transitions(self) -> None:
        """Frozen or closed accounts cannot be debited or credited."""
        acc = Account("ACC-1", "User A", Money(Decimal("100.00"), "BDT"))

        # Freeze account
        acc.freeze()
        assert acc.is_frozen()
        assert acc.status == AccountStatus.FROZEN

        with pytest.raises(AccountFrozenError, match="is FROZEN and cannot be debited"):
            acc.debit(Money(Decimal("10.00"), "BDT"))

        with pytest.raises(AccountFrozenError, match="is FROZEN and cannot be credited"):
            acc.credit(Money(Decimal("10.00"), "BDT"))

        # Reactivate account
        acc.activate()
        assert acc.is_active()
        acc.debit(Money(Decimal("10.00"), "BDT"))
        assert acc.balance == Money(Decimal("90.00"), "BDT")

        # Close account
        acc.close()
        assert acc.is_closed()
        with pytest.raises(AccountInactiveError, match="cannot be debited"):
            acc.debit(Money(Decimal("10.00"), "BDT"))

        with pytest.raises(AccountInactiveError, match="cannot be credited"):
            acc.credit(Money(Decimal("10.00"), "BDT"))


# ============================================================================
# 3. Double-Entry Bookkeeping & LedgerEntry Invariants
# ============================================================================

class TestLedgerEntryInvariants:
    """Verifies immutability, signed amount conventions, and zero-sum conservation."""

    def test_ledger_entry_is_immutable(self) -> None:
        """LedgerEntry is append-only and cannot be altered once instantiated."""
        entry = LedgerEntry.create_debit(
            entry_id="ENT-1",
            transaction_id="TX-1",
            account_id="ACC-1",
            amount=Decimal("50.00"),
            balance_after=Decimal("50.00"),
        )
        with pytest.raises(FrozenInstanceError):
            entry.amount = Decimal("-100.00")  # type: ignore[misc]

    def test_create_debit_and_credit_factory_methods(self) -> None:
        """Factory methods enforce signed conventions (debits negative, credits positive)."""
        debit = LedgerEntry.create_debit(
            entry_id="ENT-D1",
            transaction_id="TX-100",
            account_id="ACC-SENDER",
            amount=Decimal("75.50"),
            balance_after=Decimal("24.50"),
        )
        assert debit.amount == Decimal("-75.50")
        assert debit.entry_type == EntryType.DEBIT

        credit = LedgerEntry.create_credit(
            entry_id="ENT-C1",
            transaction_id="TX-100",
            account_id="ACC-RECEIVER",
            amount=Decimal("75.50"),
            balance_after=Decimal("175.50"),
        )
        assert credit.amount == Decimal("75.50")
        assert credit.entry_type == EntryType.CREDIT

    def test_double_entry_zero_sum_conservation(self) -> None:
        """Every transfer creates balanced entries where sum(Debit) + sum(Credit) == 0.00."""
        transfer_amount = Decimal("250.00")
        debit_entry = LedgerEntry.create_debit(
            "E-1", "TX-1", "ACC-A", transfer_amount, Decimal("750.00")
        )
        credit_entry = LedgerEntry.create_credit(
            "E-2", "TX-1", "ACC-B", transfer_amount, Decimal("1250.00")
        )

        net_sum = debit_entry.amount + credit_entry.amount
        assert net_sum == Decimal("0.00"), "Double-entry conservation violated!"

    def test_rejects_sign_mismatch_in_direct_instantiation(self) -> None:
        """Direct instantiation with conflicting sign and entry_type raises ValueError."""
        with pytest.raises(ValueError, match="Debit entry amount must be non-positive"):
            LedgerEntry(
                entry_id="E-1",
                transaction_id="TX-1",
                account_id="ACC-1",
                amount=Decimal("50.00"),  # Positive debit is invalid
                balance_after=Decimal("50.00"),
                entry_type=EntryType.DEBIT,
            )

        with pytest.raises(ValueError, match="Credit entry amount must be non-negative"):
            LedgerEntry(
                entry_id="E-2",
                transaction_id="TX-1",
                account_id="ACC-1",
                amount=Decimal("-50.00"),  # Negative credit is invalid
                balance_after=Decimal("50.00"),
                entry_type=EntryType.CREDIT,
            )


# ============================================================================
# 4. Transaction State Machine & GoF State Pattern Invariants
# ============================================================================

class TestTransactionStateMachine:
    """Verifies deterministic lifecycle management and GoF State Pattern enforcement."""

    def test_canonical_successful_lifecycle(self) -> None:
        """INITIATED -> LOCKED -> COMMITTED -> SETTLED."""
        fsm = TransactionStateMachine()
        assert fsm.get_state() == TransactionState.INITIATED
        assert not fsm.is_terminal()
        assert not fsm.is_settled()
        assert not fsm.is_committed()

        # Step 1: Lock acquired
        assert fsm.can_transition_to(TransactionState.LOCKED)
        fsm.transition_to(TransactionState.LOCKED)
        assert fsm.get_state() == TransactionState.LOCKED
        assert not fsm.is_committed()

        # Step 2: Ledger written & committed
        assert fsm.can_transition_to(TransactionState.COMMITTED)
        fsm.transition_to(TransactionState.COMMITTED)
        assert fsm.get_state() == TransactionState.COMMITTED
        assert fsm.is_committed()
        assert not fsm.is_settled()

        # Step 3: Settled
        assert fsm.can_transition_to(TransactionState.SETTLED)
        fsm.transition_to(TransactionState.SETTLED)
        assert fsm.get_state() == TransactionState.SETTLED
        assert fsm.is_settled()
        assert fsm.is_committed()
        assert not fsm.is_terminal()

        # Audit trail history verification
        assert fsm.history == [
            TransactionState.INITIATED,
            TransactionState.LOCKED,
            TransactionState.COMMITTED,
            TransactionState.SETTLED,
        ]

    def test_reversal_lifecycle_from_settled_dispute(self) -> None:
        """SETTLED -> REVERSED."""
        fsm = TransactionStateMachine(TransactionState.SETTLED)
        assert fsm.can_transition_to(TransactionState.REVERSED)
        fsm.transition_to(TransactionState.REVERSED)

        assert fsm.state == TransactionState.REVERSED
        assert fsm.is_terminal()
        assert fsm.can_transition_to(TransactionState.COMMITTED) is False

    def test_failure_paths_from_intermediate_states(self) -> None:
        """Transactions can fail from INITIATED, LOCKED, or COMMITTED."""
        # Fail from INITIATED (e.g. lock timeout)
        fsm1 = TransactionStateMachine(TransactionState.INITIATED)
        assert fsm1.can_transition_to(TransactionState.FAILED)
        fsm1.transition_to(TransactionState.FAILED)
        assert fsm1.state == TransactionState.FAILED
        assert fsm1.is_terminal()

        # Fail from LOCKED (e.g. insufficient funds)
        fsm2 = TransactionStateMachine(TransactionState.LOCKED)
        assert fsm2.can_transition_to(TransactionState.FAILED)
        fsm2.transition_to(TransactionState.FAILED)
        assert fsm2.state == TransactionState.FAILED
        assert fsm2.is_terminal()

        # Fail from COMMITTED (e.g. disk write failure before final settlement)
        fsm3 = TransactionStateMachine(TransactionState.COMMITTED)
        assert fsm3.can_transition_to(TransactionState.FAILED)
        fsm3.transition_to(TransactionState.FAILED)
        assert fsm3.state == TransactionState.FAILED
        assert fsm3.is_terminal()

    def test_illegal_state_transitions_raise_exception(self) -> None:
        """Illegal transitions raise InvalidStateTransitionError and IllegalStateTransitionError."""
        fsm = TransactionStateMachine()

        # INITIATED cannot jump directly to COMMITTED or SETTLED
        assert not fsm.can_transition_to(TransactionState.COMMITTED)
        with pytest.raises(
            InvalidStateTransitionError,
            match="Illegal state transition from INITIATED to COMMITTED",
        ):
            fsm.transition_to(TransactionState.COMMITTED)

        assert not fsm.can_transition_to(TransactionState.SETTLED)
        with pytest.raises(IllegalStateTransitionError):
            fsm.transition_to(TransactionState.SETTLED)

        # Transition to LOCKED
        fsm.transition_to(TransactionState.LOCKED)

        # LOCKED cannot jump directly to SETTLED or REVERSED
        assert not fsm.can_transition_to(TransactionState.SETTLED)
        with pytest.raises(InvalidStateTransitionError):
            fsm.transition_to(TransactionState.SETTLED)

        with pytest.raises(InvalidStateTransitionError):
            fsm.transition_to(TransactionState.REVERSED)

        # Transition to FAILED (terminal)
        fsm.transition_to(TransactionState.FAILED)

        # FAILED transaction can NEVER transition to any state
        for target in TransactionState:
            assert not fsm.can_transition_to(target)
            with pytest.raises(InvalidStateTransitionError):
                fsm.transition_to(target)

    def test_reversed_transaction_is_terminal(self) -> None:
        """REVERSED transaction cannot transition to any subsequent state."""
        fsm = TransactionStateMachine(TransactionState.REVERSED)
        for target in TransactionState:
            assert not fsm.can_transition_to(target)
            with pytest.raises(InvalidStateTransitionError):
                fsm.transition_to(target)

    def test_invalid_target_state_string_handling(self) -> None:
        """Passing an unrecognized state string raises InvalidStateTransitionError."""
        fsm = TransactionStateMachine()
        assert not fsm.can_transition_to("NON_EXISTENT_STATE")
        with pytest.raises(InvalidStateTransitionError, match="Invalid target state value"):
            fsm.transition_to("NON_EXISTENT_STATE")

    def test_gof_state_pattern_polymorphic_hierarchy(self) -> None:
        """Directly verifies GoF State pattern concrete state classes."""
        init_state = InitiatedState()
        assert init_state.state_name == TransactionState.INITIATED
        assert init_state.allowed_transitions() == {
            TransactionState.LOCKED,
            TransactionState.FAILED,
        }

        next_state = init_state.transition_to(TransactionState.LOCKED)
        assert isinstance(next_state, LockedState)
        assert next_state.state_name == TransactionState.LOCKED

        next_state = next_state.transition_to(TransactionState.COMMITTED)
        assert isinstance(next_state, CommittedState)

        next_state = next_state.transition_to(TransactionState.SETTLED)
        assert isinstance(next_state, SettledState)

        next_state = next_state.transition_to(TransactionState.REVERSED)
        assert isinstance(next_state, ReversedState)
        assert next_state.allowed_transitions() == set()


# ============================================================================
# 5. Pluggable Fee Calculation Strategies (GoF Strategy Pattern)
# ============================================================================

class TestFeeStrategies:
    """Verifies GoF Strategy Pattern implementations for fee calculations."""

    def test_p2p_peer_fee_strategy_zero_fee_default(self) -> None:
        """Default P2P strategy charges 0% platform fee."""
        strategy = P2PPeerFeeStrategy()
        amount = Money(Decimal("1000.00"), "BDT")
        fee = strategy.calculate_fee(amount)

        assert fee == Money(Decimal("0.00"), "BDT")
        assert fee.amount == Decimal("0.00")
        assert fee.currency == "BDT"

    def test_p2p_strategy_alias(self) -> None:
        """Verifies P2PFeeStrategy alias behaves identically."""
        strategy = P2PFeeStrategy()
        amount = Money(Decimal("500.00"), "BDT")
        assert strategy.calculate_fee(amount) == Money(Decimal("0.00"), "BDT")

    def test_p2p_strategy_with_custom_rate_and_cap(self) -> None:
        """P2P strategy supports optional percentage rate with cap."""
        strategy = P2PPeerFeeStrategy(
            rate=Decimal("0.01"),  # 1%
            flat_fee=Decimal("5.00"),  # +5 BDT
            max_fee=Money(Decimal("20.00"), "BDT"),
        )
        # 1000 * 0.01 + 5 = 15 BDT (below cap of 20)
        fee1 = strategy.calculate_fee(Money(Decimal("1000.00"), "BDT"))
        assert fee1 == Money(Decimal("15.00"), "BDT")

        # 5000 * 0.01 + 5 = 55 BDT (capped at 20 BDT)
        fee2 = strategy.calculate_fee(Money(Decimal("5000.00"), "BDT"))
        assert fee2 == Money(Decimal("20.00"), "BDT")

    def test_merchant_fee_strategy_default_rate_and_rounding(self) -> None:
        """Merchant strategy applies 1.5% fee with ROUND_HALF_UP precision."""
        strategy = MerchantFeeStrategy()  # 1.5% default

        # 100.00 * 0.015 = 1.50
        fee1 = strategy.calculate_fee(Money(Decimal("100.00"), "BDT"))
        assert fee1 == Money(Decimal("1.50"), "BDT")

        # 100.33 * 0.015 = 1.50495 -> 1.50
        fee2 = strategy.calculate_fee(Money(Decimal("100.33"), "BDT"))
        assert fee2 == Money(Decimal("1.50"), "BDT")

        # 100.37 * 0.015 = 1.50555 -> 1.51
        fee3 = strategy.calculate_fee(Money(Decimal("100.37"), "BDT"))
        assert fee3 == Money(Decimal("1.51"), "BDT")

    def test_merchant_fee_strategy_minimum_and_maximum_limits(self) -> None:
        """Merchant strategy respects min floor and max ceiling."""
        strategy = MerchantFeeStrategy(
            rate=Decimal("0.015"),
            minimum_fee=Money(Decimal("5.00"), "BDT"),
            max_fee=Money(Decimal("50.00"), "BDT"),
        )
        # 100.00 * 0.015 = 1.50 -> bounded by minimum 5.00
        fee_low = strategy.calculate_fee(Money(Decimal("100.00"), "BDT"))
        assert fee_low == Money(Decimal("5.00"), "BDT")

        # 10,000.00 * 0.015 = 150.00 -> bounded by maximum 50.00
        fee_high = strategy.calculate_fee(Money(Decimal("10000.00"), "BDT"))
        assert fee_high == Money(Decimal("50.00"), "BDT")

        # 1,000.00 * 0.015 = 15.00 -> within bounds
        fee_mid = strategy.calculate_fee(Money(Decimal("1000.00"), "BDT"))
        assert fee_mid == Money(Decimal("15.00"), "BDT")

    def test_tiered_cash_out_strategy(self) -> None:
        """Tiered Cash-Out charges 15 BDT for <= 5,000 BDT, 25 BDT for > 5,000 BDT."""
        strategy = TieredCashOutStrategy()

        # Below threshold
        fee1 = strategy.calculate_fee(Money(Decimal("2500.00"), "BDT"))
        assert fee1 == Money(Decimal("15.00"), "BDT")

        # Exactly at threshold
        fee2 = strategy.calculate_fee(Money(Decimal("5000.00"), "BDT"))
        assert fee2 == Money(Decimal("15.00"), "BDT")

        # Above threshold
        fee3 = strategy.calculate_fee(Money(Decimal("5000.01"), "BDT"))
        assert fee3 == Money(Decimal("25.00"), "BDT")

        fee4 = strategy.calculate_fee(Money(Decimal("20000.00"), "BDT"))
        assert fee4 == Money(Decimal("25.00"), "BDT")

    def test_flat_fee_strategy(self) -> None:
        """FlatFeeStrategy applies constant fee irrespective of transfer amount."""
        strategy = FlatFeeStrategy(flat_fee=Decimal("10.00"))

        fee1 = strategy.calculate_fee(Money(Decimal("100.00"), "BDT"))
        assert fee1 == Money(Decimal("10.00"), "BDT")

        fee2 = strategy.calculate_fee(Money(Decimal("10000.00"), "BDT"))
        assert fee2 == Money(Decimal("10.00"), "BDT")


# ============================================================================
# 6. Transaction Entity Lifecycle Integration
# ============================================================================

class TestTransactionEntity:
    """Verifies Transaction entity state synchronization with State Machine."""

    def test_transaction_initialization_and_transition(self) -> None:
        """Transaction entity tracks amount, fee, total debited, and state transitions."""
        tx = Transaction(
            transaction_id="TX-12345",
            from_account_id="ACC-SENDER",
            to_account_id="ACC-RECEIVER",
            amount=Money(Decimal("100.00"), "BDT"),
            fee=Money(Decimal("1.50"), "BDT"),
        )
        assert tx.state == TransactionState.INITIATED
        assert tx.total_debited_amount == Money(Decimal("101.50"), "BDT")

        # Advance state
        assert tx.can_transition_to(TransactionState.LOCKED)
        tx.transition_to(TransactionState.LOCKED)
        assert tx.state == TransactionState.LOCKED

        tx.transition_to(TransactionState.COMMITTED)
        assert tx.state == TransactionState.COMMITTED

        tx.transition_to(TransactionState.SETTLED)
        assert tx.state == TransactionState.SETTLED

    def test_transaction_rejects_currency_mismatch_between_amount_and_fee(self) -> None:
        """Amount and fee currencies must match."""
        with pytest.raises(CurrencyMismatchError):
            Transaction(
                transaction_id="TX-ERR",
                from_account_id="ACC-1",
                to_account_id="ACC-2",
                amount=Money(Decimal("100.00"), "BDT"),
                fee=Money(Decimal("1.50"), "USD"),
            )

    def test_transaction_enforces_illegal_transition_rejection(self) -> None:
        """Attempting illegal transition raises InvalidStateTransitionError."""
        tx = Transaction(
            transaction_id="TX-1",
            from_account_id="A",
            to_account_id="B",
            amount=Money(Decimal("50.00"), "BDT"),
        )
        with pytest.raises(InvalidStateTransitionError):
            tx.transition_to(TransactionState.SETTLED)


# ============================================================================
# 7. Comprehensive Type Validation & Edge-Case Coverage
# ============================================================================

class TestEdgeCasesAndValidationCoverage:
    """Verifies boundary type assertions, error contracts, and abstract bases."""

    def test_money_comparisons_against_non_money_types(self) -> None:
        """Non-Money comparisons return False or raise TypeError appropriately."""
        m = Money(Decimal("10.00"), "BDT")
        assert m != "Not a Money object"
        assert m != 100
        assert not (m == 10.0)

        with pytest.raises(TypeError):
            _ = m < 10
        with pytest.raises(TypeError):
            _ = m <= "10.00"
        with pytest.raises(TypeError):
            _ = m > 5
        with pytest.raises(TypeError):
            _ = m >= 5

    def test_account_type_validations(self) -> None:
        """Account validates balance type and rejects non-Money in operations."""
        with pytest.raises(TypeError, match="balance must be a Money instance"):
            Account("ACC-1", "Owner", "invalid_balance")  # type: ignore[arg-type]

        with pytest.raises(Exception, match="Invalid account status"):
            Account("ACC-1", "Owner", Money(Decimal("10.00"), "BDT"), status="UNKNOWN_STATUS")

        acc = Account("ACC-1", "Owner", Money(Decimal("50.00"), "BDT"))
        with pytest.raises(TypeError, match="amount must be a Money instance"):
            acc.has_sufficient_balance(50.0)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="amount must be a Money instance"):
            acc.debit(10.0)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="amount must be a Money instance"):
            acc.credit(10.0)  # type: ignore[arg-type]

    def test_ledger_entry_type_and_scale_validations(self) -> None:
        """LedgerEntry validates Decimal types and precision bounds."""
        with pytest.raises(TypeError, match="amount must be a Decimal"):
            LedgerEntry("E1", "T1", "A1", 10.0, Decimal("10.00"), EntryType.DEBIT)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="balance_after must be a Decimal"):
            LedgerEntry("E1", "T1", "A1", Decimal("-10.00"), 10.0, EntryType.DEBIT)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="amount precision cannot exceed 2 decimal places"):
            LedgerEntry("E1", "T1", "A1", Decimal("-10.001"), Decimal("10.00"), EntryType.DEBIT)

        with pytest.raises(ValueError, match="balance_after precision cannot exceed 2 decimal"):
            LedgerEntry("E1", "T1", "A1", Decimal("-10.00"), Decimal("10.005"), EntryType.DEBIT)

        with pytest.raises(ValueError, match="Invalid entry_type"):
            LedgerEntry("E1", "T1", "A1", Decimal("-10.00"), Decimal("10.00"), "INVALID_TYPE")

    def test_transaction_type_validations(self) -> None:
        """Transaction validates amount and fee Money types."""
        with pytest.raises(TypeError, match="amount must be Money"):
            Transaction("T1", "A", "B", 100.0)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="fee must be Money"):
            Transaction("T1", "A", "B", Money(Decimal("100.00"), "BDT"), fee=10.0)  # type: ignore[arg-type]

    def test_fee_strategy_currency_mismatches(self) -> None:
        """Fee strategies reject fee caps/floors configured in mismatched currencies."""
        p2p = P2PPeerFeeStrategy(rate=Decimal("0.01"), max_fee=Money(Decimal("10.00"), "USD"))
        with pytest.raises(CurrencyMismatchError, match="Fee cap currency mismatch"):
            p2p.calculate_fee(Money(Decimal("1000.00"), "BDT"))

        merchant_min = MerchantFeeStrategy(minimum_fee=Money(Decimal("5.00"), "USD"))
        with pytest.raises(CurrencyMismatchError, match="Minimum fee currency mismatch"):
            merchant_min.calculate_fee(Money(Decimal("100.00"), "BDT"))

        merchant_max = MerchantFeeStrategy(max_fee=Money(Decimal("50.00"), "USD"))
        with pytest.raises(CurrencyMismatchError, match="Maximum fee currency mismatch"):
            merchant_max.calculate_fee(Money(Decimal("10000.00"), "BDT"))

    def test_state_machine_current_state_property(self) -> None:
        """current_state property on TransactionStateMachine matches state property."""
        fsm = TransactionStateMachine()
        assert fsm.current_state == fsm.state

