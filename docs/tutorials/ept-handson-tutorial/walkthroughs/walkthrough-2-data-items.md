# Walkthrough 2 — A data item

One call: a NetCDF file becomes a STAC Item whose geometry and temporal
extent are derived from the file's own coordinates and CF attributes.

> Prerequisite: run the **session setup in the [index](../index.md)** — its
> `post()` helper, tokens and S3 credentials are used below.

**In the frontend:** *Metadata Conversion* → **NetCDF to STAC Item** —
`source_uri` =
`https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc`,
`bucket` = your bucket, `target` = `<workspace>/items/din`, `item_id` =
`<workspace>-din`, and the S3 fields; tick `overwrite`.

`POST /v1/metadata/netcdf/to-stac-item` — no manual bbox or datetime needed
when the source has CF coordinates/attributes (override via `bbox` and
`start_datetime`/`end_datetime` when it doesn't):

```bash
curl -X POST $EPT_URL/v1/metadata/netcdf/to-stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/items/din",
    "item_id": "'$WORKSPACE'-din",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true
  }' | jq '{target_uri, bbox: .stac_item.bbox}'
```

```python
DIN_NC_URL = ("https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/"
              "emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/"
              "Water_body_dissolved_inorganic_nitrogen.nc")

item = post("/v1/metadata/netcdf/to-stac-item",
            source_uri=DIN_NC_URL, bucket=BUCKET,
            target=f"{WORKSPACE}/items/din",
            item_id=f"{WORKSPACE}-din",
            overwrite=True, **S3)
print(item["target_uri"], item["stac_item"]["bbox"])
```

Notes:

- `target` is a prefix — the file is named after `item_id`
  (`<workspace>-din.json`, under `items/din/`).
- The data asset is the **NetCDF itself**; a later step converts it to Zarr
  in place.
- `collection_id` is optional — leave it out for now; the Collection step
  stamps the `collection` property on the item copies.
- `item_id` defaults to the source file name; workspace-unique ids avoid
  publish-time collisions.

**Next:** [Walkthrough 3 — Items from metadata](walkthrough-3-metadata-items.md)
