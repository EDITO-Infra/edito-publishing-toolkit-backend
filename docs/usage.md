# Using EPT

EPT is exposed through an HTTP API. This guide covers running the service directly on a machine. For container-based operation, see [Docker usage](docker-usage.md).

## Prerequisites

For a local Python installation, you need:

- Python 3.13 or newer;
- [`uv`](https://docs.astral.sh/uv/);
- network access required by EPT.

See the [install guide](install.md) for full setup instructions.

The shell examples in these guides use `bash` and [`curl`](https://curl.se/docs/) (preinstalled on Linux, macOS, and Windows 10+). In PowerShell, set environment variables with `$env:NAME = "value"` instead of `export`.

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

Read recent log entries from `logs/api.log` with any text viewer, or follow the log while EPT is running:

```bash
tail -f logs/api.log
```

In PowerShell, use `Get-Content logs/api.log -Wait` instead.

The log level can be changed with `LOG_LEVEL` (in PowerShell: `$env:LOG_LEVEL = "DEBUG"`):

```bash
export LOG_LEVEL=DEBUG
```

The log file location can be changed with `API_LOG` (in PowerShell: `$env:API_LOG = "logs/api.log"`):

```bash
export API_LOG="logs/api.log"
```

## Use the API

One possible workflow is:

1. [Exchange EDITO credentials for an access token](auth.md#response).
2. [Validate your STAC metadata](features/validate-stac.md).
3. [List the EDITO project catalogs you can publish into](features/get-my-catalogs.md).
4. [Publish your STAC catalog](features/publish-stac.md) and [follow the publication job](features/publication-jobs.md).

All features above, including STAC publication and removal, are part of the v1.0.0 release; see the [feature index](features/index.md) for the full list and detailed guides.

See the [API guide](api.md) for route and error conventions, or use Swagger UI for the generated request and response schemas.
