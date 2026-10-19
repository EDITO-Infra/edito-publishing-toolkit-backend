# Walkthrough 7 — Publish

Publishing makes the validated `stac-final` tree discoverable by every EDITO
user. Deliberate, not automatic — inspect first, then publish from the
frontend or two API calls.

## 1. Find your target catalog

**In the frontend:** *Get my catalogs* (or `GET /v1/edito/stac/mycatalogs`)
returns your catalog IDs:

```bash
curl -s $EPT_URL/v1/edito/stac/mycatalogs \
  -H "Authorization: Bearer $TOKEN" | jq .
# → ["projects/<your-catalog>", …]
```

Pick the one to publish into — the `projects/…` id is your target
`catalog_id`.

## 2. Publish

**In the frontend:** open *Publish STAC*, paste the validated catalog URL
`https://minio.dive.edito.eu/<bucket>/<workspace>/stac-final/catalog.json`
as `remote_stac_url`, pick your `projects/…` catalog id from step 1, and
run — the frontend adds the refresh-token header itself.

Via `curl`, pass the refresh token from the session setup
([index](../index.md)) as a header:

```bash
CATALOG_URL="https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/stac-final/catalog.json"

curl -s -X POST $EPT_URL/v1/edito/stac/publish \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-EDITO-Refresh-Token: $REFRESH_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"remote_stac_url\": \"$CATALOG_URL\", \"catalog_id\": \"projects/<your-catalog>\"}" \
  | jq .
```

```python
# one-off: the publish route needs the refresh-token header, so call requests
# directly instead of the post() helper
r = requests.post(f"{EPT}/v1/edito/stac/publish",
                  headers={"Authorization": f"Bearer {TOKEN}",
                           "X-EDITO-Refresh-Token": REFRESH_TOKEN},
                  json={"remote_stac_url": CATALOG_URL,
                        "catalog_id": "projects/<your-catalog>"})
r.raise_for_status()
job_id = r.json()["job_id"]
```

The call returns `202` + `job_id` — publication is queued, not done. Poll
until the job reaches a terminal status:

```bash
curl -s "$EPT_URL/v1/edito/publish/jobs/$JOB_ID?view=summary" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

```python
job = requests.get(f"{EPT}/v1/edito/publish/jobs/{job_id}?view=summary",
                   headers={"Authorization": f"Bearer {TOKEN}"}).json()
print(job["status"])          # succeeded / failed / cancelled / interrupted
```

The refresh token is required because access tokens may expire before the
queued job runs. If the job fails, check the job log (`view=detail`); most
often the `catalog_id` didn't match `^projects/…` or the catalog URL isn't
public.

## 3. Undo

`POST /v1/edito/stac/remove` with the same `catalog_id` and refresh-token
header removes what you published under that ID — destructive, use with
care.

**That's the full pass** — data converted, Items built and ARCO-ified,
Collections and Catalog assembled, validated, and live on EDITO.
