"""Transaction finite-state machine implementing the GoF State Pattern.

This module provides deterministic lifecycle management for financial transactions,
ensuring strict transition boundaries and preventing illegal financial mutations.
"""

from abc import ABC, abstractmethod
from enum import StrEnum

from app.domain.exceptions import InvalidStateTransitionError


class TransactionState(StrEnum):
    """Lifecycle states of a financial transaction."""
    INITIATED = "INITIATED"
    LOCKED = "LOCKED"
    COMMITTED = "COMMITTED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class ITransactionState(ABC):
    """Abstract State interface adhering to the GoF State Pattern."""

    @property
    @abstractmethod
    def state_name(self) -> TransactionState:
        """Returns the TransactionState enum associated with this concrete state."""
        pass

    @abstractmethod
    def allowed_transitions(self) -> set[TransactionState]:
        """Returns the set of permissible target states from this state."""
        pass

    def can_transition_to(self, target: TransactionState) -> bool:
        """Evaluates whether transitioning to the target state is permissible."""
        return target in self.allowed_transitions()

    def transition_to(self, target: TransactionState) -> "ITransactionState":
        """Executes the state transition or raises InvalidStateTransitionError."""
        if not self.can_transition_to(target):
            raise InvalidStateTransitionError(
                from_state=self.state_name,
                to_state=target,
                message=(
                    f"Illegal state transition from {self.state_name.value} to {target.value}."
                ),
            )
        return _STATE_MAP[target]


class InitiatedState(ITransactionState):
    """Initial state when transfer request is received but locks are not yet held."""

    @property
    def state_name(self) -> TransactionState:
        return TransactionState.INITIATED

    def allowed_transitions(self) -> set[TransactionState]:
        return {TransactionState.LOCKED, TransactionState.FAILED}


class LockedState(ITransactionState):
    """State where accounts are canonically locked in memory or database."""

    @property
    def state_name(self) -> TransactionState:
        return TransactionState.LOCKED

    def allowed_transitions(self) -> set[TransactionState]:
        return {TransactionState.COMMITTED, TransactionState.FAILED}


class CommittedState(ITransactionState):
    """State where account balances and ledger entries are durably mutated."""

    @property
    def state_name(self) -> TransactionState:
        return TransactionState.COMMITTED

    def allowed_transitions(self) -> set[TransactionState]:
        return {TransactionState.SETTLED, TransactionState.FAILED}


class SettledState(ITransactionState):
    """Terminal success state where funds are settled and external webhooks dispatched."""

    @property
    def state_name(self) -> TransactionState:
        return TransactionState.SETTLED

    def allowed_transitions(self) -> set[TransactionState]:
        return {TransactionState.REVERSED}


class FailedState(ITransactionState):
    """Terminal error state. No further mutations or transitions are permissible."""

    @property
    def state_name(self) -> TransactionState:
        return TransactionState.FAILED

    def allowed_transitions(self) -> set[TransactionState]:
        return set()


class ReversedState(ITransactionState):
    """Terminal dispute state. A settled transaction reversed via compensating entries."""

    @property
    def state_name(self) -> TransactionState:
        return TransactionState.REVERSED

    def allowed_transitions(self) -> set[TransactionState]:
        return set()


_STATE_MAP: dict[TransactionState, ITransactionState] = {
    TransactionState.INITIATED: InitiatedState(),
    TransactionState.LOCKED: LockedState(),
    TransactionState.COMMITTED: CommittedState(),
    TransactionState.SETTLED: SettledState(),
    TransactionState.FAILED: FailedState(),
    TransactionState.REVERSED: ReversedState(),
}


class TransactionStateMachine:
    """Finite-state machine governing the transaction lifecycle via GoF State Pattern.

    Maintains transition history for auditability and enforces strict transition
    invariants without external framework dependencies.
    """

    VALID_TRANSITIONS: dict[TransactionState, set[TransactionState]] = {
        TransactionState.INITIATED: {TransactionState.LOCKED, TransactionState.FAILED},
        TransactionState.LOCKED: {TransactionState.COMMITTED, TransactionState.FAILED},
        TransactionState.COMMITTED: {TransactionState.SETTLED, TransactionState.FAILED},
        TransactionState.SETTLED: {TransactionState.REVERSED},
        TransactionState.FAILED: set(),
        TransactionState.REVERSED: set(),
    }

    def __init__(
        self,
        initial_state: TransactionState | str = TransactionState.INITIATED,
    ) -> None:
        resolved_state = (
            TransactionState(initial_state)
            if isinstance(initial_state, str) and not isinstance(initial_state, TransactionState)
            else initial_state
        )
        self._current_state_obj: ITransactionState = _STATE_MAP[resolved_state]
        self._history: list[TransactionState] = [resolved_state]

    @property
    def state(self) -> TransactionState:
        """Current TransactionState enum value."""
        return self._current_state_obj.state_name

    @property
    def current_state(self) -> TransactionState:
        """Alias for state property."""
        return self.state

    def get_state(self) -> TransactionState:
        """Returns the current state of the transaction."""
        return self.state

    @property
    def history(self) -> list[TransactionState]:
        """Chronological audit trail of all states visited."""
        return list(self._history)

    def can_transition_to(self, target: TransactionState | str) -> bool:
        """Checks if a transition to target is valid without mutating state."""
        try:
            resolved_target = (
                TransactionState(target)
                if isinstance(target, str) and not isinstance(target, TransactionState)
                else target
            )
        except ValueError:
            return False
        return self._current_state_obj.can_transition_to(resolved_target)

    def transition_to(self, target: TransactionState | str) -> None:
        """Executes the state transition or raises InvalidStateTransitionError."""
        try:
            resolved_target = (
                TransactionState(target)
                if isinstance(target, str) and not isinstance(target, TransactionState)
                else target
            )
        except ValueError as err:
            raise InvalidStateTransitionError(
                from_state=self.state,
                to_state=target,
                message=f"Invalid target state value: '{target}'.",
            ) from err

        self._current_state_obj = self._current_state_obj.transition_to(resolved_target)
        self._history.append(resolved_target)

    def is_terminal(self) -> bool:
        """Returns True if the transaction has reached a terminal state (FAILED or REVERSED)."""
        return self.state in {TransactionState.FAILED, TransactionState.REVERSED}

    def is_settled(self) -> bool:
        """Returns True if the transaction is successfully settled."""
        return self.state == TransactionState.SETTLED

    def is_committed(self) -> bool:
        """Returns True if the transaction has been committed or settled."""
        return self.state in {TransactionState.COMMITTED, TransactionState.SETTLED}
