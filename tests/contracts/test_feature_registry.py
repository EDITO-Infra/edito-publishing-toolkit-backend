"""Contract tests for feature discovery and required test coverage metadata."""

import ast

from fastapi import APIRouter
import pytest

from ept.api.main import app
from ept.core.contracts import import_object
from ept.core.registry import FeatureRegistry
from tests.conftest import contract


@contract
def test_registry_discovers_registered_features():
    """Feature discovery should find every checked-in feature contract."""
    registry = FeatureRegistry.discover()
    assert {feature.feature.key for feature in registry.features} == {
        "catalogs.mine.get",
        "publish.job.get",
        "publish.stac",
        "remove.stac",
    }



@contract
def test_registry_validates_declared_contracts():
    """Feature contracts should resolve routers, entrypoints, models, and tests."""
    registry = FeatureRegistry.discover()
    registry.validate_repository()
    assert all(isinstance(router, APIRouter) for router in registry.routers())
    for feature in registry.features:
        assert all(callable(import_object(path)) for path in feature.entrypoints.python)


@contract
def test_every_feature_declares_unit_and_integration_tests():
    """Every feature must declare unit and integration test files."""
    registry = FeatureRegistry.discover()

    for feature in registry.features:
        assert feature.tests.unit
        assert feature.tests.integration


@contract
def test_declared_feature_test_files_contain_required_scope_markers():
    """Feature TOMLs point to files that actually contain matching decorators."""
    registry = FeatureRegistry.discover()

    for feature in registry.features:
        unit_markers = _test_scope_markers(registry.root / feature.tests.unit)
        integration_markers = _test_scope_markers(registry.root / feature.tests.integration)

        assert "unit" in unit_markers, f"{feature.feature.key} has no @unit test in {feature.tests.unit}"
        assert "integration" in integration_markers, (
            f"{feature.feature.key} has no @integration test in {feature.tests.integration}"
        )


@contract
def test_registry_rejects_feature_missing_required_test_scope(tmp_path):
    """Registry validation should fail when a feature omits a required test scope."""
    feature_dir = tmp_path / "ept" / "features" / "demo"
    test_dir = tmp_path / "tests" / "features" / "demo"
    feature_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)
    (test_dir / "test_demo.py").write_text("def test_demo():\n    pass\n", encoding="utf-8")
    (feature_dir / "feature.toml").write_text(
        """
[feature]
key = "demo"
release = "1.0.0"
title = "Demo"
description = "Demo feature."

[tests]
unit = "tests/features/demo/test_demo.py"
""",
        encoding="utf-8",
    )

    registry = FeatureRegistry.discover(tmp_path)

    registry.validate_runtime()
    with pytest.raises(ValueError, match="demo: missing integration test declaration"):
        registry.validate_repository()


@contract
def test_registered_feature_routes_are_visible_in_openapi():
    """Feature.toml route declarations should match the generated API schema."""
    paths = app.openapi()["paths"]

    for feature in FeatureRegistry.discover().features:
        for route in feature.api_routes:
            operation = paths[route.path][route.method.lower()]
            assert operation["summary"] == route.summary
            assert operation["security"] == [{"HTTPBearer": []}]
            if route.request_model:
                assert "requestBody" in operation
            else:
                assert "requestBody" not in operation
            success_responses = [
                response
                for status, response in operation["responses"].items()
                if status.startswith("2")
            ]
            assert success_responses
            assert "schema" in success_responses[0]["content"]["application/json"]


@contract
def test_api_shell_does_not_import_publishing_service_infrastructure():
    """API shell modules should not depend on PublishingService-specific infrastructure."""
    root = FeatureRegistry.discover().root

    assert not _imports_publishing_service(root / "ept/api/dependencies.py")
    assert not _imports_publishing_service(root / "ept/api/errors.py")


@contract
def test_public_feature_models_do_not_import_publishing_service_dtos():
    """Feature models are public EPT API contracts, not upstream DTO wrappers."""
    root = FeatureRegistry.discover().root
    model_paths = [
        root / "ept/features/publish_stac/models.py",
        root / "ept/features/remove_stac/models.py",
        root / "ept/features/get_publish_job/models.py",
    ]

    for path in model_paths:
        assert not _imports_publishing_service(path)


def _test_scope_markers(path) -> set[str]:
    """Return direct ``@unit`` and ``@integration`` decorators in one test file.

    The contract only accepts direct decorators on ``test_*`` functions. That
    keeps the convention simple to read and simple to validate without importing
    test modules.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    markers: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id in {"unit", "integration"}:
                markers.add(decorator.id)
    return markers


def _imported_modules(path) -> set[str]:
    """Return top-level import module names used by one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _imports_publishing_service(path) -> bool:
    """Return whether a module imports PublishingService infrastructure."""
    return any(
        module == "ept.infrastructure.services.publishing"
        or module.startswith("ept.infrastructure.services.publishing.")
        for module in _imported_modules(path)
    )
