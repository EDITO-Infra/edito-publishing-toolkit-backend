"""Repository-wide pytest configuration.

This module owns repository-wide marker policy, local test environment loading,
and live integration configuration. It also owns the release-gating policy for
feature tests: features declared for a future release (see ``feature.toml`` and
``ept.core.registry``) are not mounted, so their tests skip via
:func:`feature_is_active` instead of failing against missing routes.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ept.core.registry import FeatureRegistry
from ept.infrastructure.services.edito_auth import _jwks_client

if TYPE_CHECKING:
    from ept.infrastructure.services.edito_auth import EditoTokenResponse


SCOPE_MARKERS = {"unit", "contract", "integration"}
_test_env_loaded = False


@dataclass(frozen=True)
class LiveEditoCredentials:
    """Raw EDITO credentials loaded only for tests that request them."""

    username: str
    password: str


def _load_test_env() -> None:
    """Load local live-test settings once, when a live fixture requests them."""
    global _test_env_loaded
    if _test_env_loaded:
        return
    _test_env_loaded = True

    env_file = Path(__file__).resolve().parents[1] / "test.env"
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Require every test to declare exactly one primary scope marker."""
    invalid_items: list[str] = []
    for item in items:
        markers = {
            marker.name
            for marker in item.iter_markers()
            if marker.name in SCOPE_MARKERS
        }
        if len(markers) != 1:
            declared = ", ".join(sorted(markers)) or "none"
            invalid_items.append(f"{item.nodeid} (declared: {declared})")

    if invalid_items:
        raise pytest.UsageError(
            "Tests must declare exactly one of @unit, @contract, or @integration:\n"
            + "\n".join(f"- {item}" for item in invalid_items)
        )


@pytest.fixture(autouse=True)
def _reset_jwks_client_cache() -> None:
    """Clear the cached PyJWKClient so each test's own mocks/env take effect."""
    _jwks_client.cache_clear()


@pytest.fixture(scope="session")
def live_test_environment() -> None:
    """Load optional repository-root live-test configuration on demand."""
    _load_test_env()


@pytest.fixture(scope="session")
def live_edito_credentials(
    live_test_environment: None,
) -> LiveEditoCredentials:
    """Return raw credentials for live tests without performing authentication."""
    return LiveEditoCredentials(
        username=require_live_env("EDITO_USERNAME"),
        password=require_live_env("EDITO_PASSWORD"),
    )


@pytest.fixture(scope="session")
def live_edito_token_pair(
    live_edito_credentials: LiveEditoCredentials,
) -> EditoTokenResponse:
    """Create one fresh live token pair using the password grant."""
    from ept.infrastructure.services.edito_auth import exchange_username_password

    return asyncio.run(
        exchange_username_password(
            live_edito_credentials.username,
            live_edito_credentials.password,
        )
    )


def require_live_env(name: str) -> str:
    """Return required live-test configuration or fail clearly."""
    _load_test_env()
    value = os.getenv(name, "").strip().strip("\"'")
    if not value:
        pytest.fail(
            f"Missing required integration configuration: {name}",
            pytrace=False,
        )
    return value


def feature_is_active(key: str) -> bool:
    """Return whether a feature's routes are mounted in the active release.

    Feature tests are release-dependent like the features themselves: a feature
    declared for a later release is absent from the app (its routes return 404
    and its OpenAPI entries are gone), so its tests must skip rather than fail.
    Use this in a module-level ``pytestmark`` keyed on the feature's stable key
    from its ``feature.toml``.
    """
    registry = FeatureRegistry.discover()
    contract = next(c for c in registry.features if c.feature.key == key)
    return registry.is_active(contract)


unit = pytest.mark.unit
"""Fast test with no live network, credentials, or external dependency."""

contract = pytest.mark.contract
"""Repository contract test for metadata, registry, or release rules."""

integration = pytest.mark.integration
"""Explicitly selected test that uses live staging services."""
