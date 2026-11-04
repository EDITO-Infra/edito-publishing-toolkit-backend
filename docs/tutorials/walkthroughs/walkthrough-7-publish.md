# Walkthrough 7 — Publish

> Publishing **replaces the entire contents of the catalog it is published
> into** — if you target `projects/<project>/<catalog1>`, everything
> currently in `catalog1` is swapped for the new STAC. The project catalog
> itself and any sibling catalogs are untouched. Still, the target is
> shared: agree with your project on when to publish, and into which
> catalog. Questions? Email EDITO support or ask the trainer in a training
> session. The endpoint reference lives in
> [Publish STAC](../../features/publish-stac.md).

Publishing makes the validated `stac-final` tree visible **to your project**
in the EDITO STAC API. It does not make it public: wider visibility is a
setting granted on request to EDITO support. Inspect first, then publish from the frontend or two API calls.

## 1. Pick your target catalog

Pick the catalog **under `projects/` you want to publish into** — it must
be one **you created**: `GET /v1/edito/stac/mycatalogs` lists the project
catalogs you own, and catalogs created by other owners don't appear, even
if they are shared with you. A `$PROJECTID-catalog` id — like the one built
in [walkthrough 6](walkthrough-6-catalog.md) — is an example, not a given:
not every project has a catalog yet. An empty response just means you
haven't created one:

```bash
curl -s $EPT_URL/v1/edito/stac/mycatalogs \
  -H "Authorization: Bearer $TOKEN" | jq .
```

## 2. Publish

**In the frontend:** open *Publish STAC*, paste the validated catalog URL
`https://minio.dive.edito.eu/<bucket>/<workspace>/stac-final/catalog.json`
as `remote_stac_url`, pick your `projects/…` catalog id from step 1, and
run — the frontend adds the refresh-token header itself.

Via `curl`, pass the refresh token from the [session setup](../session-setup.md) as a header:

```bash
CATALOG_URL="https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/stac-final/catalog.json"

response=$(curl -s -X POST $EPT_URL/v1/edito/stac/jobs/publication \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-EDITO-Refresh-Token: $REFRESH_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"remote_stac_url\": \"$CATALOG_URL\", \"catalog_id\": \"projects/<your-catalog>\"}")
export JOB_ID=$(echo "$response" | jq -r .job_id)
echo "$response" | jq .
```

```python
# one-off: the publish route needs the refresh-token header, so call requests
# directly instead of the post() helper
r = requests.post(f"{EPT}/v1/edito/stac/jobs/publication",
                  headers={"Authorization": f"Bearer {TOKEN}",
                           "X-EDITO-Refresh-Token": REFRESH_TOKEN},
                  json={"remote_stac_url": CATALOG_URL,
                        "catalog_id": "projects/<your-catalog>"})
r.raise_for_status()
job_id = r.json()["job_id"]
print("queued:", job_id)
```

The call returns `202` + `job_id` — publication is queued, not done. Poll
until the job reaches a terminal status:

```bash
curl -s "$EPT_URL/v1/edito/stac/jobs/$JOB_ID?view=summary" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

```python
job = requests.get(f"{EPT}/v1/edito/stac/jobs/{job_id}?view=summary",
                   headers={"Authorization": f"Bearer {TOKEN}"}).json()
pprint(job)   # status: succeeded / failed / cancelled / interrupted
```

The refresh token is required because access tokens may expire before the
queued job runs. If the job fails, check the job log (`view=detail`); most
often the `catalog_id` didn't match `^projects/…` or the catalog URL isn't
public.

## 3. Undo

`POST /v1/edito/stac/jobs/removal` with the same `catalog_id` and refresh-token
header deletes the published catalog — its collections and items — for
every project member. It undoes a publish by deleting; there is no undo.

**That's the full pass** — data converted, Items built and ARCO-ified,
Collections and Catalog assembled, validated, and live on EDITO.
