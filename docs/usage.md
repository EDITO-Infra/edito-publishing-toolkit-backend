# Using EPT

EPT is exposed through an HTTP API. This guide covers running the service directly on a machine. For container-based operation, see [Docker usage](docker-usage.md).

## Prerequisites

For a local Python installation, you need:

- Python 3.13 or newer;
- [`uv`](https://docs.astral.sh/uv/);
- network access required by EPT.

Protected operations also require [EDITO credentials and suitable access rights](auth.md#credential-requirements).

## Run locally

Install the project dependencies:

```bash
uv sync
```

Start the API with automatic reload:

```bash
uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Alternatively, use the Make target:

```bash
make api-start
```

Set the base URL for the examples in the feature guides:

```bash
export EPT_API_URL="http://localhost:8000"
```

Verify the API:

```bash
curl "${EPT_API_URL}/v1"
```

The API is available at:

- API index: `http://localhost:8000/v1`
- Swagger UI: `http://localhost:8000/docs`
- OpenAPI schema: `http://localhost:8000/openapi.json`

## Logs

When EPT is run directly, application logs are written to both the terminal and a rotating log file.

The default file is:

```text
logs/api.log
```

Read recent log entries:

```bash
tail -n 200 logs/api.log
```

Follow the log while EPT is running:

```bash
tail -f logs/api.log
```

The log level can be changed with `LOG_LEVEL`:

```bash
export LOG_LEVEL=DEBUG
```

The log file location can be changed with `API_LOG`:

```bash
export API_LOG="logs/api.log"
```

## Use the API

A typical workflow is:

1. [Exchange EDITO credentials for an access token](auth.md#get-an-access-token).
2. [Get your catalogs and find their IDs](features/get-my-catalogs.md).
3. [Queue STAC publication](features/publish-stac.md) or [queue STAC removal](features/remove-stac.md) with a project catalog ID.
4. [Inspect the returned publication job](features/publication-jobs.md).

See the [API guide](api.md) for route and error conventions, or use Swagger UI for the generated request and response schemas.

## Hosted EDITO deployment

> **Deployment status:** EPT v1.0.0 is released, but an EDITO-hosted instance is not currently documented here.

`deploy/ept-api-service/` contains the v1.0.0 Helm chart and defaults to the `ghcr.io/edito-infra/edito-publishing-toolkit-api:1.0.0` image. Ingress remains disabled by default; operators must configure environment-specific ingress, credentials, and service settings before deployment.
