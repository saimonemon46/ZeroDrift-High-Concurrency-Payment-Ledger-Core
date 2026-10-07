"""Pytest configuration and global fixtures for LedgerCore."""

import pytest


@pytest.fixture(scope="session")
def project_root() -> str:
    """Returns the base project root directory."""
    return "ledger_core"
