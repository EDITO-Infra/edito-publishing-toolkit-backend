# Walkthrough 7 — Publish

> **A serious step — do this together with your project members.**
> Publishing is meant for a **collaborative project STAC**, not the default
> public one. Publishing **overwrites the contents of the target catalog**,
> and `remove` **deletes them for every project member** — there is no undo.
> Agree within your project who publishes, when, and into which catalog.

Publishing makes the validated `stac-final` tree visible **to your project**
in the EDITO STAC API. It does not make it public: wider visibility is a
setting granted on request to EDITO support. Inspect first, then publish from the frontend or two API calls.

## 1. Pick your target catalog

Pick a catalog **under `projects/` you can publish into**. A
`$PROJECTID-catalog` id — like the one built in
[walkthrough 6](walkthrough-6-catalog.md) — is an example, not a given: not
every project has a catalog, and membership alone does not grant publish
rights.

`GET /v1/edito/stac/mycatalogs` lists the project catalogs **you can publish
into**; an empty response just means none exists for you yet:

```bash
curl -s $EPT_URL/v1/edito/stac/mycatalogs \
  -H "Authorization: Bearer $TOKEN" | jq .
```

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
header deletes the published catalog — its collections and items — for
every project member. It undoes a publish by deleting; there is no undo.

**That's the full pass** — data converted, Items built and ARCO-ified,
Collections and Catalog assembled, validated, and live on EDITO.
