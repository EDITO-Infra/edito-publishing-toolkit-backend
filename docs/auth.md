# Authentication

EPT authenticates users through EDITO Keycloak. The API exchanges EDITO credentials for an access token and validates that token before protected requests reach a feature service.

## Credential requirements

You need an EDITO account to call protected endpoints. Publishing or removing STAC also requires permission for the relevant EDITO project catalog.

If you are unsure which rights your account has, contact [EDITO Support](mailto:support@edito.eu). See [Onboard your Data on EDITO](https://help.edito.eu/en/articles/15339950-onboard-your-data-on-edito) for the wider data-onboarding process.

## Authentication levels

| Level | Used by | Required headers |
| --- | --- | --- |
| Public credential exchange | `POST /v1/auth` | None |
| Protected feature request | Current feature endpoints | `Authorization: Bearer <access_token>` |

Individual feature guides document any additional requirements for their operations.

## Get an access token

Set the URL of the EPT instance:

```bash
export EPT_API_URL="http://localhost:8000"
```

`POST /v1/auth` is public. Exchange an EDITO username and password:

```bash
curl --fail-with-body -X POST "${EPT_API_URL}/v1/auth" \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "password",
    "username": "YOUR_EDITO_USERNAME",
    "password": "YOUR_EDITO_PASSWORD"
  }'
```

Use the tokens from the successful response for protected requests:

```bash
export ACCESS_TOKEN="<access_token>"
export REFRESH_TOKEN="<refresh_token>"
```

The access token authorizes protected routes. Publication and removal requests also send the refresh token in the `X-EDITO-Refresh-Token` header.

The current implementation forwards the credentials to the configured EDITO Keycloak token endpoint and does not persist them.

## Python (`httpx`)

Keep credentials outside the source code, for example in environment variables:

```python
import os

import httpx

base_url = os.getenv("EPT_API_URL", "http://localhost:8000")

with httpx.Client(base_url=base_url, timeout=30.0) as client:
    response = client.post(
        "/v1/auth",
        json={
            "grant_type": "password",
            "username": os.environ["EDITO_USERNAME"],
            "password": os.environ["EDITO_PASSWORD"],
        },
    )
    response.raise_for_status()
    tokens = response.json()
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

headers = {"Authorization": f"Bearer {access_token}"}
```

Do not print or log complete tokens.

## Refresh an access token

Exchange a refresh token without resending the username and password:

```bash
curl --fail-with-body -X POST "${EPT_API_URL}/v1/auth" \
  -H "Content-Type: application/json" \
  -d "{
    \"grant_type\": \"refresh_token\",
    \"refresh_token\": \"${REFRESH_TOKEN}\"
  }"
```

Replace both local token values with the tokens returned by a successful refresh.

## Call protected routes

Send the access token on every protected request:

```text
Authorization: Bearer <access_token>
```

EPT validates the token signature and issuer against EDITO Keycloak JWKS and checks that the token belongs to the expected client.

## Swagger UI

In Swagger UI, select **Authorize** and enter the access token only. Swagger adds the `Bearer` prefix automatically. Consult the relevant [feature guide](features/index.md) for any operation-specific fields or headers.

## Token safety

- Never commit credentials or tokens to source control.
- Do not write complete authentication tokens to logs.
- Send credentials and tokens only to an EPT service URL you trust.
- Treat publication and removal rights independently from successful authentication: a valid account may still lack authorization for a project.
