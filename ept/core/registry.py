"""Feature contract discovery, release gating, and validation.

Every feature declares the release it ships in via ``[feature] release`` in its
``feature.toml``. The registry mounts only the routers of features whose release
is at or below the active release (``EPT_ACTIVE_RELEASE``, default ``1.1.0``), so
features built for a future release can live in the tree — fully validated and
tested — without exposing routes, models, or OpenAPI entries in the current one.

Runtime validation covers only deployable application artifacts, and applies to
all discovered features including not-yet-released ones: their code and declared
dependencies must still import cleanly. Repository validation additionally
requires each feature's declared test files, which are part of a valid source
repository but not a production installation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from .contracts import FeatureContract, import_object, load_feature_contract


def _release_tuple(release: str) -> tuple[int, ...]:
    """Parse a ``1.2.3`` release string into a comparable tuple.

    Release comparisons are component-wise numeric (``1.10.0 > 1.9.0``), which
    plain string comparison would get wrong.
    """
    try:
        return tuple(int(part) for part in release.strip().split("."))
    except ValueError as exc:
        raise ValueError(f"Invalid release string '{release}'. Use 'MAJOR.MINOR.PATCH'.") from exc


@dataclass(frozen=True)
class FeatureRegistry:
    """In-memory index of discovered feature contracts, gated by release.

    All discovered features are kept in ``features`` regardless of release so
    validation sees the whole tree; only :meth:`is_active` features contribute
    routers to the mounted API.
    """

    root: Path
    features: list[FeatureContract]

    @classmethod
    def discover(cls, root: Path | None = None) -> FeatureRegistry:
        """Discover feature contracts in the repository and return a registry instance."""
        repo_root = root or Path(__file__).resolve().parents[2]
        paths = sorted(repo_root.glob("ept/features/**/feature.toml"))
        return cls(repo_root, [load_feature_contract(path) for path in paths])

    def validate_runtime(self) -> None:
        """Validate production requirements without requiring repository tests."""
        self._raise_validation_errors(self._runtime_validation_errors())

    def validate_repository(self) -> None:
        """Validate runtime requirements plus declared repository test files."""
        errors = self._runtime_validation_errors()
        for contract in self.features:
            for scope, test_path in (
                ("unit", contract.tests.unit),
                ("integration", contract.tests.integration),
            ):
                # A single file may satisfy both scopes, but both declarations
                # are required so release checks can reason about coverage.
                if not test_path:
                    errors.append(f"{contract.feature.key}: missing {scope} test declaration.")
                    continue
                if not (self.root / test_path).exists():
                    errors.append(f"{contract.feature.key}: missing {scope} test file '{test_path}'.")
        self._raise_validation_errors(errors)

    def _runtime_validation_errors(self) -> list[str]:
        """Return feature validation errors relevant to production startup."""
        errors: list[str] = []
        keys = [contract.feature.key for contract in self.features]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            errors.append(f"Duplicate feature keys: {', '.join(duplicates)}")

        available = set(keys)
        allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        for contract in self.features:
            feature = contract.feature
            for field_name, value in (
                ("key", feature.key),
                ("release", feature.release),
                ("title", feature.title),
                ("description", feature.description),
            ):
                if not value:
                    errors.append(
                        f"{contract.source_path}: feature {field_name} is required."
                    )
            for dependency in feature.depends_on:
                if dependency not in available:
                    errors.append(f"{feature.key}: missing dependency '{dependency}'.")
            if contract.api.router:
                self._validate_router(contract.api.router, errors)
            for entrypoint in contract.entrypoints.python:
                self._validate_callable(entrypoint, errors)
            for route in contract.api_routes:
                if not route.method or not route.path:
                    errors.append(f"{feature.key}: API routes require method and path.")
                elif route.method not in allowed_methods:
                    errors.append(
                        f"{feature.key}: unsupported API route method '{route.method}'."
                    )
                for model in (route.request_model, route.response_model):
                    if model:
                        self._validate_model(model, errors)
            return errors

    @staticmethod
    def _raise_validation_errors(errors: list[str]) -> None:
        """Raise one readable error containing all registry validation failures."""
        if errors:
            raise ValueError(
                "Feature registry validation failed:\n"
                + "\n".join(f"- {error}" for error in errors)
            )

    def active_release(self) -> tuple[int, ...]:
        """Return the currently released version features are gated against.

        ``EPT_ACTIVE_RELEASE`` defaults to ``1.1.0``. Features whose declared
        release is higher exist in the repository but are not mounted, so the
        public API surface matches what this deployment actually supports.
        """
        return _release_tuple(os.getenv("EPT_ACTIVE_RELEASE", "1.1.0"))

    def is_active(self, contract: FeatureContract) -> bool:
        """Return whether a feature's release is at or below the active release.

        Inactive features remain fully validated and discoverable; only their
        routes are withheld. Bump ``EPT_ACTIVE_RELEASE`` (not the code) to mount
        them once their external dependency — e.g. the EDITO STAC API
        background service — is available.
        """
        return _release_tuple(contract.feature.release) <= self.active_release()

    def routers(self) -> list[APIRouter]:
        """Return API routers of active-release features only.

        Routers of features declared for later releases are skipped, which also
        keeps them out of the generated OpenAPI schema.
        """
        routers: list[APIRouter] = []
        for contract in self.features:
            if not self.is_active(contract):
                continue
            if contract.api.router:
                router = import_object(contract.api.router)
                if not isinstance(router, APIRouter):
                    raise TypeError(f"API router '{contract.api.router}' is not an APIRouter.")
                routers.append(router)
        return routers

    @staticmethod
    def _validate_router(path: str, errors: list[str]) -> None:
        """Validate that the given path points to an APIRouter."""
        try:
            router = import_object(path)
        except ValueError as exc:
            errors.append(str(exc))
            return
        if not isinstance(router, APIRouter):
            errors.append(f"API router '{path}' is not an APIRouter.")

    @staticmethod
    def _validate_callable(path: str, errors: list[str]) -> None:
        """Validate that the given path points to a callable object."""
        try:
            entrypoint = import_object(path)
        except ValueError as exc:
            errors.append(str(exc))
            return
        if not callable(entrypoint):
            errors.append(f"Entrypoint '{path}' is not callable.")

    @staticmethod
    def _validate_model(path: str, errors: list[str]) -> None:
        """Validate that the given path points to a Pydantic model class."""
        try:
            model = import_object(path)
        except ValueError as exc:
            errors.append(str(exc))
            return
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            errors.append(f"Model '{path}' is not a Pydantic model class.")
