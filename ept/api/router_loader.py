"""Registry-driven API router loading.

The API shell discovers feature routers from ``feature.toml`` files. Startup
validates production feature artifacts only; repository tests separately verify
the declared test files. Feature routes declare their own auth dependencies so
the route signature, generated OpenAPI, and runtime behavior show what each
endpoint requires.

Mounting is release-dependent: only features whose ``[feature] release`` is at
or below ``EPT_ACTIVE_RELEASE`` contribute routes (see ``ept.core.registry``).
"""

from fastapi import FastAPI

from ept.core.registry import FeatureRegistry


def include_feature_routers(app: FastAPI, registry: FeatureRegistry | None = None) -> None:
    """Attach the routers of all active-release features.

    Features declared for later releases are validated but not mounted, so
    their endpoints stay absent from the app and its OpenAPI schema.
    """
    feature_registry = registry or FeatureRegistry.discover()
    # Production startup intentionally does not require repository test files.
    feature_registry.validate_runtime()
    for router in feature_registry.routers():
        app.include_router(router)
