# Publish STAC

Queue a publication job that publishes a STAC Catalog into the EDITO STAC API under a project catalog. The job runs asynchronously; check the status or the detailed logs using [Publication jobs](publication-jobs.md).

> Publishing **replaces the entire contents of the catalog it is published
> into** — if you target `projects/<project>/<catalog1>`, everything
> currently in `catalog1` is swapped for the new STAC. The project catalog
> itself and any sibling catalogs are untouched. Still, the target is
> shared: agree with your project on when to publish, and into which
> catalog. Questions? Email EDITO support.

## Endpoint

```text
POST /v1/edito/stac/jobs/publication
```

Authentication: `Authorization: Bearer <access_token>` **and** `X-EDITO-Refresh-Token: <refresh_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/edito/stac/jobs/publication" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-EDITO-Refresh-Token: ${REFRESH_TOKEN}" \
  -d '{
    "remote_stac_url": "https://minio.dive.edito.eu/project-{my-project-id}/project-stac/catalog.json",
    "catalog_id": "projects/{my-project-id}"
  }'
```

### Python (`httpx`)

```python
import os

import httpx

response = httpx.post(
    f"{os.environ['EPT_API_URL']}/v1/edito/stac/jobs/publication",
    headers={
        "Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}",
        "X-EDITO-Refresh-Token": os.environ["REFRESH_TOKEN"],
    },
    json={
        "remote_stac_url": "https://minio.dive.edito.eu/oidc-myusername/mystac/catalog.json",
        "catalog_id": "projects/demo",
    },
    timeout=30.0,
)
response.raise_for_status()
print(response.json()["job_id"])
```

## Request

The public request accepts only `remote_stac_url` and `catalog_id`:

- `remote_stac_url` — URL of the STAC catalog to publish, for example in your EDITO object storage.
- `catalog_id` — the EDITO project catalog to publish into, such as `projects/{project-id}`. Use [Get my catalogs](get-my-catalogs.md) to list the project catalog IDs you own; an empty array means you have not created any project catalogs yet.

## Response

Responds `202 Accepted`:

```json
{
  "job_id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "status": "queued"
}
```

Use the returned `job_id` with [publication-jobs](./publication-jobs.md).

## Good to know

- A refresh token is required because an access token may expire before the queued job is processed.
- Publishing replaces all STAC inside the target catalog. `POST /v1/edito/stac/jobs/removal` goes further: it deletes the published catalog for every project member — see [Remove STAC](remove-stac.md).
