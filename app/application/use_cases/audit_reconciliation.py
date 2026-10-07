"""Audit Reconciliation Use Case for LedgerCore.

Performs mathematical verification of the core double-entry bookkeeping invariant:
1. Total system net of all ledger entries must be strictly 0.00 (Zero-Sum Law).
2. Every account balance must strictly reconcile with its historical ledger journal.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.application.exceptions import ReconciliationDivergenceError
from app.domain.models import LedgerEntry
from app.ports.repository_ports import IAccountRepository, ILedgerRepository


@dataclass(frozen=True)
class AccountDiscrepancy:
    """Details of a single account failing reconciliation."""

    account_id: str
    actual_balance: Decimal
    expected_balance: Decimal
    discrepancy: Decimal


@dataclass(frozen=True)
class ReconciliationReport:
    """Comprehensive mathematical ledger audit report."""

    total_account_balances: Decimal
    total_ledger_credits: Decimal
    total_ledger_debits: Decimal
    total_system_net: Decimal
    is_system_net_zero: bool
    is_balanced: bool
    accounts_audited_count: int
    ledger_entries_count: int
    discrepancies: list[AccountDiscrepancy] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serializes report to dictionary."""
        return {
            "total_account_balances": str(self.total_account_balances),
            "total_ledger_credits": str(self.total_ledger_credits),
            "total_ledger_debits": str(self.total_ledger_debits),
            "total_system_net": str(self.total_system_net),
            "is_system_net_zero": self.is_system_net_zero,
            "is_balanced": self.is_balanced,
            "accounts_audited_count": self.accounts_audited_count,
            "ledger_entries_count": self.ledger_entries_count,
            "discrepancies": [
                {
                    "account_id": d.account_id,
                    "actual_balance": str(d.actual_balance),
                    "expected_balance": str(d.expected_balance),
                    "discrepancy": str(d.discrepancy),
                }
                for d in self.discrepancies
            ],
            "timestamp": self.timestamp,
        }


class AuditReconciliationUseCase:
    """Audits system-wide financial invariants and pinpoints divergences."""

    def __init__(
        self,
        account_repo: IAccountRepository,
        ledger_repo: ILedgerRepository,
    ) -> None:
        self.account_repo = account_repo
        self.ledger_repo = ledger_repo

    def reconcile(self, raise_on_error: bool = False) -> ReconciliationReport:
        """Executes a full mathematical reconciliation of the ledger system.

        Args:
            raise_on_error: If True, raises ReconciliationDivergenceError on divergence.

        Returns:
            ReconciliationReport: Audit findings and discrepancy breakdown.

        Raises:
            ReconciliationDivergenceError: If raise_on_error is True and divergence is detected.
        """
        now = time.time()
        accounts = self.account_repo.get_all()
        all_entries = self.ledger_repo.get_all_entries()

        # 1. Calculate system-wide ledger totals
        total_credits = Decimal("0.00")
        total_debits = Decimal("0.00")
        total_system_net = Decimal("0.00")

        entries_by_account: dict[str, list[LedgerEntry]] = defaultdict(list)
        for entry in all_entries:
            entries_by_account[entry.account_id].append(entry)
            total_system_net += entry.amount
            if entry.amount > Decimal("0.00"):
                total_credits += entry.amount
            else:
                total_debits += entry.amount

        is_system_net_zero = (total_system_net == Decimal("0.00"))

        # 2. Reconcile each individual account
        total_account_balances = Decimal("0.00")
        discrepancies: list[AccountDiscrepancy] = []

        for acc in accounts:
            total_account_balances += acc.balance.amount
            acc_entries = entries_by_account.get(acc.account_id, [])

            if acc_entries:
                # Chronologically sorted: latest entry's balance_after must match current balance
                latest_entry = max(acc_entries, key=lambda e: (e.timestamp, e.entry_id))
                expected = latest_entry.balance_after
                diff = acc.balance.amount - expected

                if diff != Decimal("0.00"):
                    discrepancies.append(
                        AccountDiscrepancy(
                            account_id=acc.account_id,
                            actual_balance=acc.balance.amount,
                            expected_balance=expected,
                            discrepancy=diff,
                        )
                    )

        is_balanced = is_system_net_zero and (len(discrepancies) == 0)

        report = ReconciliationReport(
            total_account_balances=total_account_balances,
            total_ledger_credits=total_credits,
            total_ledger_debits=total_debits,
            total_system_net=total_system_net,
            is_system_net_zero=is_system_net_zero,
            is_balanced=is_balanced,
            accounts_audited_count=len(accounts),
            ledger_entries_count=len(all_entries),
            discrepancies=discrepancies,
            timestamp=now,
        )

        if raise_on_error and not is_balanced:
            raise ReconciliationDivergenceError(
                divergence=total_system_net,
                details=report.to_dict(),
            )

        return report
