# Test Setup

Run the default unit and contract suite from the repository root:

```bash
uv run pytest
```

## Test scopes

- `@unit`: fast, isolated tests with no live network.
- `@contract`: repository feature, registry, or source-contract checks.
- `@integration`: explicitly selected checks that may use live services,
  credentials, or local format integrations.

Scope decorators are imported from `tests.conftest`. Every test must declare
exactly one primary scope marker; collection fails for missing or conflicting scopes.

```bash
uv run pytest -m integration
uv run pytest -m "contract or unit or integration"
```

Repository-wide environment loading and marker policy live in
`tests/conftest.py`. Feature dependency overrides live in
`tests/features/conftest.py`. Reusable outbound-service test doubles live in
`tests/infrastructure`; this keeps the global test configuration independent of
individual services.

## Registry validation

Application startup uses `FeatureRegistry.validate_runtime()`, which checks only
production feature metadata and imports. Repository contract tests use
`validate_repository()`, which additionally requires every feature's declared
unit and integration test files. Tests are therefore required for a valid source
repository, but they are not packaged into the production application.

## Local live-test configuration

Put local values in repository-root `test.env`, export them in the shell, or
provide them through CI `env:`. Live fixtures load `test.env` lazily, and
shell/CI values take precedence.

Selecting integration tests is the explicit opt-in for live publication and
removal. Tests that need EDITO authentication request a shared token fixture,
which creates a fresh access/refresh pair from `EDITO_USERNAME` and
`EDITO_PASSWORD`. Tokens are passed directly to requests and are never installed
in the process environment.

### Publication integration matrix

| Check | Behavior | Target configuration |
| --- | --- | --- |
| Live publishing API contract | Read-only | Optional `PUBLISHING_API_URL`; otherwise uses the default publishing service URL |
| Infrastructure publication dry-run | Non-mutating | `PUBLISH_REMOTE_STAC_URL`, `PUBLISH_PROJECT_ID` |
| Feature publication | Mutating | `PUBLISH_REMOTE_STAC_URL`, `PUBLISH_PROJECT_ID` |
| Feature removal | Destructive | `REMOVE_PROJECT_CATALOG_ID` |
| Publication-job lookup | Read-only | `EDITO_DEMO_JOB_ID` |

Authenticated integration tests require `EDITO_USERNAME` and `EDITO_PASSWORD`.
The authentication integration tests perform independent real password- and
refresh-token exchanges and use `EDITO_STAC_API` to verify that
`preferred_username` is available and the access token is accepted. Each
publishing test validates its own target configuration from the matrix above.

Example `test.env` entries:

```dotenv
# Optional override; omit this to use the default publishing service URL.
# PUBLISHING_API_URL=https://edito-publisher.vliz.be
EDITO_USERNAME=...
EDITO_PASSWORD=...

PUBLISH_PROJECT_ID=projects/edito-demo-project
PUBLISH_REMOTE_STAC_URL=https://s3.waw3-1.cloudferro.com/emodnet/publishertests/test-metadata/edito-demo-project/catalog.json
REMOVE_PROJECT_CATALOG_ID=projects/edito-removal-test-project
EDITO_DEMO_JOB_ID=...
EDITO_STAC_API=...
```

`PUBLISHING_API_URL` selects the publishing service used by live requests and
its `/openapi.json` contract check. `EDITO_AUTH_URL` may be set when the default
EDITO authentication endpoint is not appropriate.

## FastAPI feature tests

Feature route tests call the application through `httpx.ASGITransport`, so they
exercise routing, validation, dependency injection, and response serialization
without starting Uvicorn. Unit tests use a stable authenticated principal and a
recording outbound HTTP fake; authentication and gateway behavior are tested
separately under `tests/api` and `tests/infrastructure`.
