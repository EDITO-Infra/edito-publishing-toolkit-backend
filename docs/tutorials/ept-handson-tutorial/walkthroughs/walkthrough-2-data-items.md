# Walkthrough 2 — A data item

One call: a NetCDF file becomes a STAC Item whose geometry and temporal
extent are derived from the file's own coordinates and CF attributes. Then a
second call makes it yours — a custom id, in the folder the rest of the
session uses.

> Prerequisite: run the **session setup in the [index](../index.md)** — its
> `post()` helper, tokens and S3 credentials are used below.

## 1. Let EPT name it

**In the frontend:** *Metadata Conversion* → **NetCDF to STAC Item** —
`source_uri` =
`https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc`,
`bucket` = your bucket, `target` = `<workspace>/items/din-auto`, the S3
fields, tick `overwrite` — and **no `item_id`**.

`POST /v1/metadata/netcdf/to-stac-item` — no manual bbox or datetime needed
when the source has CF coordinates/attributes (override via `bbox` and
`start_datetime`/`end_datetime` when it doesn't):

```bash
curl -X POST $EPT_URL/v1/metadata/netcdf/to-stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/items/din-auto",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true
  }' | tee item-auto.json | jq '{id: .stac_item.id, target_uri, bbox: .stac_item.bbox}'
```

```python
DIN_NC_URL = ("https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/"
              "emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/"
              "Water_body_dissolved_inorganic_nitrogen.nc")

item = post("/v1/metadata/netcdf/to-stac-item",
            source_uri=DIN_NC_URL, bucket=BUCKET,
            target=f"{WORKSPACE}/items/din-auto",
            overwrite=True, **S3)
print(item["stac_item"]["id"], item["target_uri"], item["stac_item"]["bbox"])
```

No `item_id`, no problem: EPT proposes one — from the dataset's own `id`,
`identifier`, or `title` attribute (else the source file name), namespaced
with your username. The proposed id is in the response (`stac_item.id`) and
names the output file (see `target_uri`). Two caveats: the proposal is
*your* namespace, not the project's, and identifiers can be long and
machine-flavoured — exactly why the next step exists.

Notes:

- The data asset is the **NetCDF itself**; a later step converts it to Zarr
  in place.
- `collection_id` is optional — leave it out for now; the Collection step
  stamps the `collection` property on the item copies.

## 2. Make it yours

The first response already carries the full Item — set the id you want and
rebuild it into the folder the rest of the session uses. The builder takes
the Item as a base and your fields override it.

**In the frontend:** *Make STAC* → **Build STAC Item** — copy the Item JSON
from the conversion response, paste it as `stac_json`, set `id` =
`<projectid>-din`, keep `bucket`, and set `target` =
`<workspace>/items/din/<projectid>-din.json` (ending in `.json` names the
file itself), tick `overwrite`, export.

```bash
# the first response already carries the full Item:
jq '.stac_item' item-auto.json > din-auto-item.json

curl -X POST $EPT_URL/v1/stac/build/item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --slurpfile i din-auto-item.json \
        --arg bucket "$BUCKET" --arg target "$WORKSPACE/items/din/$PROJECTID-din.json" \
        --arg endpoint "https://minio.dive.edito.eu" \
        --arg key "$S3_KEY" --arg secret "$S3_SECRET" --arg token "$S3_SESSION" \
        '{stac_json: $i[0], id: "'$PROJECTID'-din",
          bucket: $bucket, target: $target,
          endpoint_url: $endpoint, access_key_id: $key,
          secret_access_key: $secret, session_token: $token, overwrite: true}')" \
  | jq .target_uri
```

```python
mine = item["stac_item"]
post("/v1/stac/build/item",
     stac_json=mine, id=f"{PROJECTID}-din",
     bucket=BUCKET, target=f"{WORKSPACE}/items/din/{PROJECTID}-din.json",
     overwrite=True, **S3)
```

This is the same *suggest → make it yours* rhythm the Collection and Catalog
walkthroughs use: let EPT derive everything, then override the fields you
care about. Here it pins the id to `<projectid>-din` — your project's short
name (it **must be unique**; every id in this session derives from it),
project-scoped so everyone in the session sees the same ids — and lands the
file at the exact path the following walkthroughs read:

✅ **Check:** `items/din/<projectid>-din.json` exists, its `id` is
`<projectid>-din`, and the NetCDF asset is unchanged.

**Next:** [Walkthrough 3 — Items from metadata](walkthrough-3-metadata-items.md)
