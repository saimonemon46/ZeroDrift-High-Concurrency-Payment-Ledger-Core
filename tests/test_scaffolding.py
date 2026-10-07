"""Verification test for Phase 0: Workspace Setup and Clean Architecture Scaffolding."""

import importlib
from pathlib import Path


def test_clean_architecture_packages_exist() -> None:
    """Verifies all expected hexagonal architectural layers exist as importable packages."""
    packages = [
        "app",
        "app.domain",
        "app.domain.strategies",
        "app.ports",
        "app.application",
        "app.application.use_cases",
        "app.application.decorators",
        "app.infrastructure",
        "app.infrastructure.concurrency",
        "app.infrastructure.dsa",
        "app.infrastructure.os_threading",
        "app.infrastructure.resilience",
        "app.infrastructure.persistence",
        "app.api",
        "app.api.routes",
    ]

    for pkg in packages:
        module = importlib.import_module(pkg)
        assert module is not None, f"Failed to import package: {pkg}"


def test_directory_structure_completeness() -> None:
    """Verifies that the physical directory tree contains all required directories."""
    base_dir = Path(__file__).resolve().parent.parent

    expected_directories = [
        base_dir / "app" / "domain",
        base_dir / "app" / "ports",
        base_dir / "app" / "application",
        base_dir / "app" / "infrastructure",
        base_dir / "app" / "api",
        base_dir / "tests",
        base_dir / "benchmarks",
        base_dir / "docs",
    ]

    for directory in expected_directories:
        assert directory.exists() and directory.is_dir(), (
            f"Expected directory does not exist: {directory}"
        )
