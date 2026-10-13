# EPT API

EPT exposes a single HTTP API for preparing and publishing metadata and data products into EDITO. The service is a FastAPI application assembled from independent feature slices under `ept/features/`; each feature declares its routes and models in a colocated `feature.toml` manifest.

See [Using EPT](usage.md) to start the service.

## API discovery

With the default local configuration, the service is available at `http://localhost:8000`.

- `GET /v1` returns the API index.
- `GET /docs` serves Swagger UI with the full request and response models.
- `GET /openapi.json` returns the generated OpenAPI schema.

Swagger UI and the OpenAPI schema are the authoritative contract for the released API. Clients should rely on documented contracts rather than undocumented implementation details.

## Service routes

These routes belong to the API shell itself and are always present.

| Method | Path | Purpose | Authentication |
| --- | --- | --- | --- |
| `GET` | `/` | Redirect to the versioned API index | Public |
| `GET` | `/v1` | Return service and documentation links | Public |
| `GET` | `/docs` | Serve Swagger UI | Public |
| `GET` | `/openapi.json` | Return the OpenAPI schema | Public |
| `GET` | `/healthz` | Return process health status | Public |
| `POST` | `/v1/auth` | Exchange EDITO credentials or a refresh token for tokens, plus temporary S3 credentials | Public |

See [Authentication](auth.md) for token exchange and header requirements.

## Feature routes

Everything beyond the service routes above is provided by a feature. Each feature guide documents its own endpoints, authentication, and usage — for example, [Validate STAC](features/validate-stac.md) exposes `POST /v1/stac/validate` to check a remote STAC catalog, collection, or item before publishing.

Browse all available features in the [feature index](features/index.md).

## Versioning

EPT v1.0.0 exposes its supported public contract under `/v1`. The unversioned `/` route exists only to redirect users to the API index.

Backward-compatible additions may be made within `/v1`. Breaking changes require a new major API namespace.

## Errors

API failures use Problem Details responses with the media type:

```text
application/problem+json
```

Responses can include a human-readable `detail`, a stable machine-readable `reason`, the request `instance`, and structured validation errors. Clients should primarily branch on the HTTP status and stable `reason` value rather than matching human-readable text.
