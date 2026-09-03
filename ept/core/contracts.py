"""Feature contract data structures and TOML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class FeatureMetadata:
    """Identity, release, and dependency metadata from ``[feature]``."""

    key: str
    release: str
    title: str
    description: str
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PackageMetadata:
    """Optional package extra and dependency metadata from ``[package]``."""

    extra: str | None = None
    requires: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EntrypointMetadata:
    """Python callable entrypoints declared by one feature."""

    python: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ApiMetadata:
    """API router import path declared by one feature."""

    router: str | None = None


@dataclass(frozen=True)
class ApiRoute:
    """One public API route declared in a feature contract."""

    method: str
    path: str
    summary: str | None = None
    request_model: str | None = None
    response_model: str | None = None


@dataclass(frozen=True)
class TestMetadata:
    """Feature test declarations from the flat ``[tests]`` TOML table."""

    unit: str = ""
    integration: str = ""


@dataclass(frozen=True)
class FeatureContract:
    """Parsed representation of one feature's ``feature.toml`` contract."""

    feature: FeatureMetadata
    package: PackageMetadata
    entrypoints: EntrypointMetadata
    api: ApiMetadata
    api_routes: list[ApiRoute]
    tests: TestMetadata
    source_path: Path


def import_object(import_path: str) -> object:
    """Import an attribute from a `module:attribute` string."""
    if ":" not in import_path:
        raise ValueError(f"Invalid import path '{import_path}'. Use 'module:attribute'.")
    module_path, attribute = import_path.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ValueError(f"Import '{import_path}' could not load module '{module_path}'.") from exc
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ValueError(f"Import '{import_path}' did not resolve to an attribute.") from exc


def _as_list(value: Any) -> list[str]:
    """Normalize optional TOML arrays into a list of strings."""
    if not value:
        return []
    if not isinstance(value, list):
        raise TypeError("Feature list declarations must be TOML arrays.")
    return [str(item) for item in value]


def _test_metadata(value: Any) -> TestMetadata:
    """Parse flat [tests] scope entries.

    A feature can keep all tests in one Python file, but it must still declare
    which file carries unit coverage and which file carries integration coverage.
    """
    if not value:
        return TestMetadata()
    if not isinstance(value, dict):
        raise TypeError("Feature test declarations must be TOML tables of id = path.")
    # Scope is carried by keys in one flat table, not nested [tests.unit] tables.
    if any(isinstance(test_path, dict) for test_path in value.values()):
        raise TypeError("Feature test declarations must use flat [tests] scope = path entries.")
    return TestMetadata(
        unit=str(value.get("unit", "")),
        integration=str(value.get("integration", "")),
    )


def parse_feature_contract(data: dict[str, Any], source_path: Path) -> FeatureContract:
    """Parse one `feature.toml` payload."""
    feature_data = data.get("feature", {})
    package_data = data.get("package", {})
    entrypoint_data = data.get("entrypoints", {})
    api_data = data.get("api", {}) or {}
    tests_data = data.get("tests", {})
    routes = [
        ApiRoute(
            method=str(route.get("method", "")).upper(),
            path=str(route.get("path", "")),
            summary=route.get("summary"),
            request_model=route.get("request_model"),
            response_model=route.get("response_model"),
        )
        for route in api_data.get("routes", [])
    ]
    return FeatureContract(
        feature=FeatureMetadata(
            key=str(feature_data.get("key", "")),
            release=str(feature_data.get("release", "")),
            title=str(feature_data.get("title", "")),
            description=str(feature_data.get("description", "")),
            depends_on=_as_list(feature_data.get("depends_on")),
        ),
        package=PackageMetadata(
            extra=package_data.get("extra"),
            requires=_as_list(package_data.get("requires")),
        ),
        entrypoints=EntrypointMetadata(python=_as_list(entrypoint_data.get("python"))),
        api=ApiMetadata(router=api_data.get("router")),
        api_routes=routes,
        tests=_test_metadata(tests_data),
        source_path=source_path,
    )


def load_feature_contract(path: Path) -> FeatureContract:
    """Load and parse one feature contract file from disk."""
    return parse_feature_contract(tomllib.loads(path.read_text(encoding="utf-8")), path)
