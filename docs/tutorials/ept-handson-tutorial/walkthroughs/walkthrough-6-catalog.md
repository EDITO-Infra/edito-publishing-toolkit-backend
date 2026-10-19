# Walkthrough 6 — Catalog

The Collections under `collections/` are complete but disconnected — each
one valid on its own, none knowing the root. One call reads them and
assembles the final catalog: links taken care of, tree exported ready to
publish.

Works with the EPT frontend or `curl`/`python`. Requires the exported
Collections and a public workspace folder.

## 1. Suggest and assemble in one call

**In the frontend:** *Make STAC* → **Suggest STAC Catalog** — paste these
two collection URLs into `collection_links` (a JSON array of strings):

```json
[
  "https://minio.dive.edito.eu/<bucket>/<workspace>/collections/<workspace>-habitats-and-heritage/collection.json",
  "https://minio.dive.edito.eu/<bucket>/<workspace>/collections/<workspace>-ocean-chemistry/collection.json"
]
```

Then set `id` = `<workspace>-catalog`, a `title` and `description` of your
choice, `bucket` = your bucket, `target` = `<workspace>/stac-final`, tick
`overwrite`.

`POST /v1/stac/suggest/catalog` does the whole assembly:

1. fetches and validates each Collection you pass in `collection_links`;
2. fetches each Collection's item files via its `item` links;
3. enriches the catalog metadata from the collections — keywords unioned,
   providers merged (shown in `warnings`);
4. exports a self-contained tree: `catalog.json` at the `stac-final/` root
   with relative `child` links (`collections/<id>/collection.json`), each
   Collection copied under `stac-final/collections/<id>/` with its
   `root`/`parent` links re-pointed at the catalog, item copies re-linked.

The response says `status: "completed"` and `exported: true`, with
`catalog_uri`, `collection_uris` and `item_uris` pointing at what landed.

```bash
curl -X POST $EPT_URL/v1/stac/suggest/catalog \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "collection_links": [
      "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/collections/'$WORKSPACE'-habitats-and-heritage/collection.json",
      "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/collections/'$WORKSPACE'-ocean-chemistry/collection.json"
    ],
    "id": "'$WORKSPACE'-catalog",
    "title": "EPT hands-on tutorial",
    "description": "Datasets converted with the EDITO Publishing Toolkit.",
    "bucket": "'$BUCKET'",
    "target": "'$WORKSPACE'/stac-final",
    "overwrite": true,
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'"
  }' | tee catalog-suggested.json \
     | jq '{status, exported, catalog_uri, collection_uris, item_uris, warnings}'
```

```python
collection_urls = [
    f"{MINIO}/{BUCKET}/{WORKSPACE}/collections/{cid}/collection.json"
    for cid in (f"{WORKSPACE}-habitats-and-heritage",     # exported earlier
                f"{WORKSPACE}-ocean-chemistry")]          # your-turn collection

catalog = post("/v1/stac/suggest/catalog",
     collection_links=collection_urls,
     id=f"{WORKSPACE}-catalog",
     title="EPT hands-on tutorial",
     description="Datasets converted with the EDITO Publishing Toolkit.",
     bucket=BUCKET, target=f"{WORKSPACE}/stac-final",
     overwrite=True, **S3)
print(catalog["status"], catalog["exported"], catalog["catalog_uri"])
print("\n".join(catalog["warnings"]))
```

Skipped the DIN collection? Drop its URL from the list.

Alternative: instead of `collection_links`, pass `source_folder` =
`s3://$BUCKET/$WORKSPACE/collections` — the folder of collection trees,
each subfolder one self-contained Collection. Same result.

## 2. Check the tree

`catalog.json` exists at the `stac-final/` root and shows its child links.
Validate the whole tree through its public URL:

**In the frontend:** *STAC Tools* → **Validate STAC** — `stac_url` =
`https://minio.dive.edito.eu/<bucket>/<workspace>/stac-final/catalog.json`,
`validate_children` **on** (every Item and Collection inside is checked
too).

```bash
curl -s -X POST $EPT_URL/v1/stac/validate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"stac_url": "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/stac-final/catalog.json",
       "validate_children": true}' \
  | jq '{stac_is_valid, summary}'
```

```python
post("/v1/stac/validate",
     stac_url=f"{MINIO}/{BUCKET}/{WORKSPACE}/stac-final/catalog.json",
     validate_children=True)
```

✅ **Check:** `stac_is_valid: true` and `invalid_objects: 0`. Expected
layout (one file per item, nested by item id):

```
stac-final/
├── catalog.json
└── collections/
    ├── <workspace>-habitats-and-heritage/
    │   ├── collection.json
    │   ├── <workspace>-mangrove/<workspace>-mangrove.json
    │   └── <workspace>-heritage-shipwrecks/<workspace>-heritage-shipwrecks.json
    └── <workspace>-ocean-chemistry/
        ├── collection.json
        └── <workspace>-din/<workspace>-din.json
```

## 3. Make it yours (optional)

Edit the catalog's title/description: fetch `catalog.json`, edit, rebuild
at the same target with `overwrite`. The child links travel inside the
JSON, so they survive the rebuild.

**In the frontend:** *Make STAC* → **Build STAC Catalog** — open the
`catalog.json` URL above in a browser, copy the JSON, paste it as
`stac_json`, edit `title` and `description` in the JSON, keep `bucket` +
`target` = `<workspace>/stac-final`, `overwrite` on, export.

```bash
curl -s "$EPT_URL/v1/stac/get?url=https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/stac-final/catalog.json" \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.stac_object | .title = "Your title" | .description = "Your description"' > my-catalog.json

curl -X POST $EPT_URL/v1/stac/build/catalog \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --slurpfile c my-catalog.json \
        --arg bucket "$BUCKET" --arg target "$WORKSPACE/stac-final" \
        --arg endpoint "https://minio.dive.edito.eu" \
        --arg key "$S3_KEY" --arg secret "$S3_SECRET" --arg token "$S3_SESSION" \
        '{stac_json: $c[0], bucket: $bucket, target: $target,
          endpoint_url: $endpoint, access_key_id: $key,
          secret_access_key: $secret, session_token: $token, overwrite: true}')" \
  | jq .target_uri
```

```python
mine = requests.get(f"{MINIO}/{BUCKET}/{WORKSPACE}/stac-final/catalog.json").json()
mine["title"] = "Your title"
mine["description"] = "Your description"
post("/v1/stac/build/catalog", stac_json=mine,
     bucket=BUCKET, target=f"{WORKSPACE}/stac-final", overwrite=True, **S3)
```

## 4. The pipeline rule

Each layer is generated from the one before: change an Item → re-run the
Collection export that gathers it; change a Collection → re-run this
walkthrough. Each re-run is one call at the same target with `overwrite` on
— nothing goes stale silently.

## 5. Assemble is optional now

`/v1/stac/assemble` is no longer part of the flow — the tree is assembled
by construction. Keep it as a power tool for relocating or merging trees:
export `stac-final` elsewhere and re-link, one call.

Before publishing, open the tree in the
[NetCDF/Zarr viewer](https://datalab.dive.edito.eu/launcher/service-playground/netcdf-zarr-viewer)
and
[Parquet explorer](https://datalab.dive.edito.eu/launcher/service-playground/parquet-explorer)
one last time — the next step publishes this same
`stac-final/catalog.json` URL.

**Next:** [Walkthrough 7 — Publish](walkthrough-7-publish.md)
