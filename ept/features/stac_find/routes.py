"""FastAPI routes for the STAC find feature."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ept.api.dependencies import AuthFailureDetail, EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses
from ept.infrastructure.services.editostacapi import EditoStacApiClient

from . import service
from .models import (
    FindCatalogsResponse,
    FindCollectionsResponse,
    FindItemsResponse,
    FindPropertiesResponse,
)

router = APIRouter(prefix="/v1/stac/find", tags=["STAC Tools", "find"])


def _parse_bbox(bbox: str | None) -> list[float] | None:
    if not bbox:
        return None
    values = [v.strip() for v in bbox.split(",") if v.strip()]
    try:
        numbers = [float(v) for v in values]
    except ValueError:
        numbers = []
    if len(numbers) not in (4, 6) or len(numbers) != len(values):
        raise HTTPException(
            status_code=422,
            detail="bbox must be 4 (or 6) comma-separated numbers: minx,miny,maxx,maxy.",
        )
    return numbers


def _parse_csv(values: str | None) -> list[str] | None:
    if not values:
        return None
    items = [v.strip() for v in values.split(",") if v.strip()]
    return items or None


def _parse_match(match: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for part in match.split(","):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            raise HTTPException(
                status_code=422,
                detail="match must be comma-separated key=value pairs, e.g. platform=Sentinel-1.",
            )
        pairs[key] = value
    return pairs


def _require_user(edito_auth: EditoBearerAuthDep) -> str:
    if not edito_auth.username:
        raise HTTPException(
            status_code=401,
            detail=AuthFailureDetail(
                reason="invalid_bearer_token",
                message="The bearer token is not valid.",
            ).__dict__,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return edito_auth.access_token


@router.get(
    "/items",
    response_model=FindItemsResponse,
    summary="Find items (STAC Item Search)",
    operation_id="findStacItemsV1",
    description=(
        "Run a STAC Item Search (GET /search) against the EDITO STAC API. "
        "`bbox` is `minx,miny,maxx,maxy` (or 6 values with altitude), "
        "`datetime` a STAC datetime range, `collections`/`ids` "
        "comma-separated lists, and `q` a case-insensitive free-text filter "
        "on item id/title/description. Requires `Authorization: Bearer "
        "<access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="EDITO STAC API"),
)
async def find_items_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    bbox: str | None = None,
    datetime: str | None = Query(default=None, description="STAC datetime or range, e.g. 2024-01-01T00:00:00Z/2024-12-31T23:59:59Z"),
    collections: str | None = Query(default=None, description="Comma-separated collection ids"),
    ids: str | None = Query(default=None, description="Comma-separated item ids"),
    limit: int = Query(default=10, ge=1, le=1000),
    q: str | None = Query(default=None, description="Case-insensitive free-text filter"),
) -> FindItemsResponse:
    """Find items via the shared EDITO STAC API client."""
    access_token = _require_user(edito_auth)
    api = EditoStacApiClient(http_client)
    return await service.find_item(
        api,
        access_token=access_token,
        bbox=_parse_bbox(bbox),
        datetime=datetime,
        collections=_parse_csv(collections),
        ids=_parse_csv(ids),
        limit=limit,
        q=q,
    )


@router.get(
    "/items/scientific",
    response_model=FindItemsResponse,
    summary="Find items by scientific metadata (DOI, citation)",
    operation_id="findStacScientificItemsV1",
    description=(
        "Find items carrying STAC Scientific extension metadata. `doi` matches "
        "`sci:doi` and `sci:publications[].doi`; `citation` matches "
        "`sci:citation` and publication citations as a case-insensitive "
        "substring. A server-side filter is attempted first; the EDITO STAC "
        "API does not expose scientific properties as filterable queryables, "
        "so the search falls back to a client-side scan. `collections` entries "
        "may be exact collection ids or name fragments. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="EDITO STAC API"),
)
async def find_scientific_items_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    doi: str | None = Query(default=None, description="DOI to match exactly (case-insensitive), e.g. 10.14279/depositonce-12345"),
    citation: str | None = Query(default=None, description="Citation fragment (case-insensitive substring)"),
    collections: str | None = Query(default=None, description="Comma-separated collection ids or name fragments"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> FindItemsResponse:
    """Find items by scientific metadata via the shared EDITO STAC API client."""
    if not doi and not citation:
        raise HTTPException(
            status_code=422,
            detail="Either 'doi' or 'citation' must be provided.",
        )
    access_token = _require_user(edito_auth)
    return await service.find_item_scientific(
        EditoStacApiClient(http_client),
        access_token=access_token,
        doi=doi,
        citation=citation,
        collections=_parse_csv(collections),
        limit=limit,
    )


@router.get(
    "/collections",
    response_model=FindCollectionsResponse,
    summary="Find collections",
    operation_id="findStacCollectionsV1",
    description=(
        "List collections from the EDITO STAC API (following `next` pages) and "
        "narrow them with a case-insensitive free-text `q` over id, title, "
        "description, and keywords. Requires `Authorization: Bearer "
        "<access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="EDITO STAC API"),
)
async def find_collections_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000, description="Page size requested per upstream page"),
) -> FindCollectionsResponse:
    """Find collections via the shared EDITO STAC API client."""
    access_token = _require_user(edito_auth)
    return await service.find_collection(
        EditoStacApiClient(http_client), access_token=access_token, q=q, limit=limit
    )


@router.get(
    "/catalogs",
    response_model=FindCatalogsResponse,
    summary="Find catalogs in the STAC catalog tree",
    operation_id="findStacCatalogsV1",
    description=(
        "Walk the EDITO STAC API catalog tree breadth-first from the projects "
        "root (`/catalogs/projects`), following every `rel=\"child\"` link so "
        "nested child catalogs are included (each level fetched concurrently), "
        "and filter them by a case-insensitive free-text `q` over link title "
        "and href. Traversal is deduplicated on href and depth-capped. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="EDITO STAC API"),
)
async def find_catalogs_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    q: str | None = None,
) -> FindCatalogsResponse:
    """Find catalogs in the catalog tree via the shared EDITO STAC API client."""
    access_token = _require_user(edito_auth)
    return await service.find_catalog(
        EditoStacApiClient(http_client), access_token=access_token, q=q
    )


@router.get(
    "/assets",
    response_model=FindItemsResponse,
    summary="Find items by asset",
    operation_id="findStacAssetsV1",
    description=(
        "Run an item find on the EDITO STAC API and return a FeatureCollection of "
        "items with an asset whose key, href, or title matches the free-text `q`; "
        "each returned item keeps only its matched assets. `collections` entries "
        "may be exact collection ids or name fragments — they are resolved against "
        "the collection catalog first (see /collections). Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="EDITO STAC API"),
)
async def find_assets_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    q: str,
    collections: str | None = Query(default=None, description="Comma-separated collection ids or name fragments"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> FindItemsResponse:
    """Find assets via the shared EDITO STAC API client."""
    access_token = _require_user(edito_auth)
    return await service.find_asset(
        EditoStacApiClient(http_client),
        access_token=access_token,
        q=q,
        collections=_parse_csv(collections),
        limit=limit,
    )


@router.get(
    "/properties",
    response_model=FindPropertiesResponse,
    summary="Find items by properties",
    operation_id="findStacPropertiesV1",
    description=(
        "Run an item find on the EDITO STAC API and keep items whose "
        "properties match every `match` pair, e.g. "
        "`match=platform=Sentinel-1` or a citation/DOI fragment like "
        "`match=sci:doi=10.14279`. Keys compare exactly; values compare as "
        "case-insensitive substrings. Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="EDITO STAC API"),
)
async def find_properties_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    match: str = Query(..., description="Comma-separated property key=value pairs"),
    collections: str | None = Query(default=None, description="Comma-separated collection ids or name fragments"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> FindPropertiesResponse:
    """Find properties via the shared EDITO STAC API client."""
    access_token = _require_user(edito_auth)
    return await service.find_properties(
        EditoStacApiClient(http_client),
        access_token=access_token,
        match=_parse_match(match),
        collections=_parse_csv(collections),
        limit=limit,
    )
