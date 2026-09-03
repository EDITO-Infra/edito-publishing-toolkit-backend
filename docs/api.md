# EPT API

The EPT API is a FastAPI application assembled from feature slices under `ept/features/`. Each registered feature declares its route and public models in a colocated `feature.toml` manifest.

See [Using EPT](usage.md) to start the service.

## API discovery

With the default local configuration, the service is available at `http://localhost:8000`.

- `GET /v1` returns the API index.
- `GET /docs` serves Swagger UI.
- `GET /openapi.json` returns the generated OpenAPI schema.

Swagger UI and the OpenAPI schema provide the detailed request and response models for the released EPT v1 API. Clients should rely on documented contracts rather than undocumented implementation details.

## Current routes

| Method | Path | Purpose | Authentication |
| --- | --- | --- | --- |
| `GET` | `/` | Redirect to the versioned API index | Public |
| `GET` | `/v1` | Return service and documentation links | Public |
| `GET` | `/docs` | Serve Swagger UI | Public |
| `GET` | `/openapi.json` | Return the OpenAPI schema | Public |
| `GET` | `/healthz` | Return process health status | Public |
| `POST` | `/v1/auth` | Exchange EDITO credentials or a refresh token for tokens | Public |
| `GET` | `/v1/edito/stac/mycatalogs` | List catalog IDs you have access to | Access token |
| `POST` | `/v1/edito/stac/publish` | Queue STAC publication | Access token and refresh token |
| `POST` | `/v1/edito/stac/remove` | Queue STAC removal | Access token and refresh token |
| `GET` | `/v1/edito/publish/jobs/{job_id}` | Read a publication job summary or logs | Access token |

See [Authentication](auth.md) for token exchange and header requirements.

## Versioning

EPT v1.0.0 exposes its supported public contract under `/v1`. The unversioned `/` route exists only to redirect users to the API index.

Backward-compatible additions may be made within `/v1`. Breaking changes require a new major API namespace.

## Asynchronous publication flow

STAC publication and removal are asynchronous:

1. Submit a publish or remove request.
2. EPT returns HTTP `202 Accepted` with a queued job ID.
3. Pass the job ID to `GET /v1/edito/publish/jobs/{job_id}`.
4. Request summary, detail, or raw-log views until the job reaches a terminal state.

Detailed examples are available in:

- [Get my catalogs](features/get-my-catalogs.md)
- [Publish STAC](features/publish-stac.md)
- [Remove STAC](features/remove-stac.md)
- [Publication jobs](features/publication-jobs.md)

## Errors

API failures use Problem Details responses with the media type:

```text
application/problem+json
```

Responses can include a human-readable `detail`, a stable machine-readable `reason`, the request `instance`, and structured validation errors. Clients should primarily branch on the HTTP status and stable `reason` value rather than matching human-readable text.
