"""Event Publisher Port for LedgerCore.

Defines domain event definitions and the publication contract adhering to
the Observer Pattern for asynchronous side-effects (notifications, audit, fraud).
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.domain.value_objects import Money


@dataclass(frozen=True)
class TransactionSettledEvent:
    """Domain event emitted when a financial transaction is settled."""
    transaction_id: str
    from_account_id: str
    to_account_id: str
    amount: Money
    fee: Money = field(default_factory=lambda: Money.zero())
    timestamp: float = field(default_factory=time.time)


class IEventPublisher(ABC):
    """Abstract port for asynchronous domain event dissemination."""

    @abstractmethod
    def publish(self, event: TransactionSettledEvent) -> None:
        """Publishes a TransactionSettledEvent to downstream handlers or message brokers."""
        pass

    @abstractmethod
    def publish_generic(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publishes an arbitrary domain event by name and dictionary payload."""
        pass
