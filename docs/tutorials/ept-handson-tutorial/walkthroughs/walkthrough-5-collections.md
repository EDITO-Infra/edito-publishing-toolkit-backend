# Walkthrough 5 — Collections

Wrap the hosted Items in a Collection — one call suggests it from the Items
and exports a complete, self-contained Collection folder: `collection.json`
plus one copy of each Item, links wired, ready to open and validate on its
own. No separate builder step, no assemble later.

> Before running: make your workspace folder public (EDITO datalab → Storage
> → the folder's eyeball icon) — the remaining walkthroughs read Items,
> Collections and the final catalog by their public URLs (viewers, validate,
> publish). World-readable while on: demo only, toggle it back off after the
> session.

Works with the EPT frontend or `curl`/`python` — both shown. Requires the
hosted Items, ARCO-ified in the previous step.

## 1. Suggest and export in one call

**In the frontend:** *Make STAC* → **Suggest STAC Collection from items** —
paste these **two** hosted Item URLs into `item_links` (a JSON array of
strings):

```json
[
  "https://minio.dive.edito.eu/<bucket>/<workspace>/items/mangrove/<workspace>-mangrove.json",
  "https://minio.dive.edito.eu/<bucket>/<workspace>/items/heritage-shipwrecks/<workspace>-heritage-shipwrecks.json"
]
```

Then set `id` = `<workspace>-habitats-and-heritage`, `bucket` = your bucket,
  `target` = `<workspace>/collections/<workspace>-habitats-and-heritage`, and
tick `overwrite`.

These are the two mangrove/heritage Items — the DIN Item stays out:
it belongs to the other Collection, built in the **your turn** section at
the bottom of this page.

`POST /v1/stac/suggest/collection` fetches the Items and derives everything:
the extent is the union of their bboxes and datetimes, a shared license
becomes the Collection license (distinct licenses become a `licenses` list),
keywords are unioned, and the id/title are suggested too. Nothing here is
hand-written — you only pick the destination and the id.

The id is yours to pick — flat, no `/`, workspace-unique; EPT proposes one
if you leave it out. Nothing needs to match beforehand: the export in the
same call stamps the id onto the item copies (`collection` property plus
`root`/`parent`/`collection` links), so `collection.json` plus one
subfolder per item (`<item-id>/<item-id>.json`) lands complete and
self-rooted. The response says `status: "completed"` and `exported: true`,
with `collection_uri` and `item_uris` pointing at what landed.

```bash
curl -X POST $EPT_URL/v1/stac/suggest/collection \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "item_links": [
      "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/items/mangrove/'$WORKSPACE'-mangrove.json",
      "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/items/heritage-shipwrecks/'$WORKSPACE'-heritage-shipwrecks.json"
    ],
    "id": "'$WORKSPACE'-habitats-and-heritage",
    "bucket": "'$BUCKET'",
    "target": "'$WORKSPACE'/collections/'$WORKSPACE'-habitats-and-heritage",
    "overwrite": true,
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'"
  }' | tee collection-suggested.json \
     | jq '{status, exported, collection_uri, item_uris, warnings}'
```

```python
my_id = f"{WORKSPACE}-habitats-and-heritage"   # any flat, workspace-unique id works

collection = post("/v1/stac/suggest/collection",
     item_links=[
         f"{MINIO}/{BUCKET}/{WORKSPACE}/items/mangrove/{WORKSPACE}-mangrove.json",
         f"{MINIO}/{BUCKET}/{WORKSPACE}/items/heritage-shipwrecks/{WORKSPACE}-heritage-shipwrecks.json",
     ],
     id=my_id,
     bucket=BUCKET, target=f"{WORKSPACE}/collections/{my_id}",
     overwrite=True, **S3)
print(collection["status"], collection["exported"], collection["collection_uri"])
print("\n".join(collection["warnings"]))
```

Check `.warnings` for anything EPT had to guess — extent, license and
keywords are derived, and the export writes them unchanged.

## 2. Read it back and validate it alone

The response's `collection_uri` is the public URL of `collection.json` —
open it in a browser and the item links are there: the folder is complete
on its own. Check what landed with *STAC Fetch*: the `id` is yours,
`extent` covers both Items, `license` matches.

```bash
MY_ID=$WORKSPACE-habitats-and-heritage

curl -s "$EPT_URL/v1/stac/get?url=https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/collections/$MY_ID/collection.json" \
  -H "Authorization: Bearer $TOKEN" | jq '.stac_object | .id, .extent, .license'
```

Then validate the folder where it stands — `validate_children` checks the
Collection **and** its item copies:

**In the frontend:** *STAC Tools* → **Validate STAC** — `stac_url` =
`https://minio.dive.edito.eu/<bucket>/<workspace>/collections/<workspace>-habitats-and-heritage/collection.json`,
`validate_children` **on**.

```bash
curl -s -X POST $EPT_URL/v1/stac/validate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"stac_url": "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/collections/'$MY_ID'/collection.json",
       "validate_children": true}' \
  | jq '{stac_is_valid, summary}'
```

```python
post("/v1/stac/validate",
     stac_url=f"{MINIO}/{BUCKET}/{WORKSPACE}/collections/{my_id}/collection.json",
     validate_children=True)
```

✅ **Check:** `stac_is_valid: true` and `invalid_objects: 0`. This is the
partway check the old flow never had — the bare `collection.json` from the
builder had dangling links until assemble ran; here the exported folder is
complete and every object inside it passes schema validation.

## 3. Make it yours

Read what was suggested and change what you don't like — title and
description are yours to call. Rebuilding at the same target with
`overwrite` is a one-call update at the same URL; the `id` stays fixed —
it's what the exported item copies carry and what the catalog links under.

**In the frontend:** *STAC Collection Builder* — open the
`collection.json` URL above in a browser, copy the JSON, paste it as
`stac_json`, edit `title` and `description` in the JSON, keep `bucket` +
`target` = `<workspace>/collections/<workspace>-habitats-and-heritage`,
`overwrite` on, export.

```bash
curl -s "$EPT_URL/v1/stac/get?url=https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/collections/$MY_ID/collection.json" \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.stac_object | .title = "Your title" | .description = "Your description"' > my-collection.json

curl -X POST $EPT_URL/v1/stac/build/collection \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --slurpfile c my-collection.json \
        --arg bucket "$BUCKET" --arg target "$WORKSPACE/collections/$MY_ID" \
        --arg endpoint "https://minio.dive.edito.eu" \
        --arg key "$S3_KEY" --arg secret "$S3_SECRET" --arg token "$S3_SESSION" \
        '{stac_json: $c[0], bucket: $bucket, target: $target,
          endpoint_url: $endpoint, access_key_id: $key,
          secret_access_key: $secret, session_token: $token, overwrite: true}')" \
  | jq .target_uri
```

```python
mine = requests.get(
    f"{MINIO}/{BUCKET}/{WORKSPACE}/collections/{my_id}/collection.json"
).json()
mine["title"] = "Your title"
mine["description"] = "Your description"
post("/v1/stac/build/collection", stac_json=mine,
     bucket=BUCKET, target=f"{WORKSPACE}/collections/{my_id}",
     overwrite=True, **S3)
```

The builder writes only `collection.json` — the item copies from step 1
stay put, still linked.

## Your turn — a Collection for the DIN item

Same flow, one Item, in the frontend too: *Make STAC* → **Suggest STAC
Collection from items** — `item_links` = this single URL:

```json
[
  "https://minio.dive.edito.eu/<bucket>/<workspace>/items/din/<workspace>-din.json"
]
```

`id` = `<workspace>-ocean-chemistry`, `bucket` = your bucket, `target` =
`<workspace>/collections/<workspace>-ocean-chemistry`, `overwrite` on —
then read it back and validate exactly as in steps 1–2 above.

Keep that id — the catalog step links the Collection under it, so a
descriptive name pays off.

**Next:** [Walkthrough 6 — Catalog](walkthrough-6-catalog.md)
