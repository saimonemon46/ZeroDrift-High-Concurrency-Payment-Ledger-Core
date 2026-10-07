"""Repository Port contracts for LedgerCore.

Defines persistence interfaces for Account and LedgerEntry entities adhering to
the Dependency Inversion Principle (Clean/Hexagonal Architecture).
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from app.domain.models import Account, LedgerEntry
from app.domain.value_objects import Money


class IAccountRepository(ABC):
    """Abstract port for account persistence operations."""

    @abstractmethod
    def get_by_id(self, account_id: str) -> Account | None:
        """Retrieves an Account by its unique account_id. Returns None if not found."""
        pass

    @abstractmethod
    def save(self, account: Account) -> None:
        """Persists or updates an Account entity."""
        pass

    @abstractmethod
    def get_all(self) -> list[Account]:
        """Returns all accounts in the repository for audit and reconciliation."""
        pass

    def create(self, account: Account) -> Account:
        """Creates and saves a new account, returning the persisted instance."""
        self.save(account)
        return account

    def update_balance(self, account_id: str, new_balance: Money) -> None:
        """Updates the balance of an account by ID."""
        acc = self.get_by_id(account_id)
        if acc is None:
            raise KeyError(f"Account {account_id} not found.")
        acc.balance = new_balance
        self.save(acc)


class ILedgerRepository(ABC):
    """Abstract port for double-entry bookkeeping ledger persistence."""

    @abstractmethod
    def append_entry(self, entry: LedgerEntry) -> None:
        """Appends an immutable LedgerEntry record."""
        pass

    @abstractmethod
    def get_entries_for_account(self, account_id: str) -> list[LedgerEntry]:
        """Returns chronological ledger entries associated with the account."""
        pass

    @abstractmethod
    def get_all_entries(self) -> list[LedgerEntry]:
        """Returns all ledger entries in the system for auditing."""
        pass

    def append_entries(self, entries: list[LedgerEntry]) -> None:
        """Appends multiple ledger entries in sequence."""
        for entry in entries:
            self.append_entry(entry)

    def get_entries_by_account(self, account_id: str) -> list[LedgerEntry]:
        """Alias for get_entries_for_account."""
        return self.get_entries_for_account(account_id)

    def get_total_system_net(self) -> Decimal:
        """Calculates total net sum across all ledger entries."""
        return sum((e.amount for e in self.get_all_entries()), start=Decimal("0.00"))
