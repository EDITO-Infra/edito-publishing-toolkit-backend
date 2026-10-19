# Walkthrough 4 — ARCO items

Convert the data assets of the three Items you created to ARCO formats — one
call per Item:

- **DIN** — `.nc` asset, auto-detected → `nc-to-zarr`
- **Shipwrecks** — plain `.zip` asset, ambiguous — map it explicitly →
  `gdb-to-parquet`
- **Mangrove** — `.zip` of ESRI shapefiles (no `.gdb` inside), map it
  explicitly → `shp-to-parquet`

`POST /v1/arco/stac-item` reads a hosted Item, converts its data assets,
adds the converted assets to the Item, and writes the updated Item back.
With an `s3://` source and **no `item_target`**, it re-exports **in place**
— same file, new `zarr`/`parquet` assets alongside the originals. Converted
data lands under `<workspace>/data/`.

Works with the EPT frontend or `curl`/`python` — every step shows both.

## 1. DIN — NetCDF asset → Zarr

**In the frontend:** *Make STAC* → **ARCO STAC Item** — `source_uri` =
`s3://<bucket>/<workspace>/items/din/<workspace>-din.json`, `bucket` = your
bucket, `assets_target` = `<workspace>/data/din`, leave `conversions` empty,
tick `overwrite`. No `item_target` → in place.

```bash
curl -X POST $EPT_URL/v1/arco/stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "s3://'$BUCKET'/'$WORKSPACE'/items/din/'$WORKSPACE'-din.json",
    "bucket": "'$BUCKET'",
    "assets_target": "'$WORKSPACE'/data/din",
    "conversions": {},
    "overwrite": true,
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'"
  }' | jq '{target_uri, conversions}'
```

```python
post("/v1/arco/stac-item",
     source_uri=f"s3://{BUCKET}/{WORKSPACE}/items/din/{WORKSPACE}-din.json",
     bucket=BUCKET, assets_target=f"{WORKSPACE}/data/din",
     conversions={},           # the .nc asset is auto-detected
     overwrite=True, **S3)
```

## 2. Shipwrecks — gdb zip → GeoParquet

The shipwrecks archive contains a file geodatabase. Same call as DIN, with
an explicit `conversions` mapping (a plain `.zip` is never auto-converted):

**In the frontend:** *Make STAC* → **ARCO STAC Item** — `source_uri` =
`s3://<bucket>/<workspace>/items/heritage-shipwrecks/<workspace>-heritage-shipwrecks.json`,
`assets_target` = `<workspace>/data/heritage-shipwrecks`, `conversions` =
`{".zip": "gdb-to-parquet"}`, tick `overwrite`.

```bash
# shipwrecks
curl -X POST $EPT_URL/v1/arco/stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "s3://'$BUCKET'/'$WORKSPACE'/items/heritage-shipwrecks/'$WORKSPACE'-heritage-shipwrecks.json",
    "bucket": "'$BUCKET'",
    "assets_target": "'$WORKSPACE'/data/heritage-shipwrecks",
    "conversions": {".zip": "gdb-to-parquet"},
    "overwrite": true,
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'"
  }' | jq '{target_uri, conversions}'
```

```python
post("/v1/arco/stac-item",
     source_uri=(f"s3://{BUCKET}/{WORKSPACE}/items/heritage-shipwrecks/"
                 f"{WORKSPACE}-heritage-shipwrecks.json"),
     bucket=BUCKET, assets_target=f"{WORKSPACE}/data/heritage-shipwrecks",
     conversions={".zip": "gdb-to-parquet"},
     overwrite=True, **S3)
```

## 3. Mangrove — shapefile zip → GeoParquet

The mangrove archive contains ESRI shapefiles (no `.gdb`), so it maps to
`shp-to-parquet` — one GeoParquet file per `.shp` inside.

**In the frontend:** *Make STAC* → **ARCO STAC Item** — `source_uri` =
`s3://<bucket>/<workspace>/items/mangrove/<workspace>-mangrove.json`,
`assets_target` = `<workspace>/data/mangrove`, `conversions` =
`{".zip": "shp-to-parquet"}`, tick `overwrite`.

```bash
# mangrove
curl -X POST $EPT_URL/v1/arco/stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "s3://'$BUCKET'/'$WORKSPACE'/items/mangrove/'$WORKSPACE'-mangrove.json",
    "bucket": "'$BUCKET'",
    "assets_target": "'$WORKSPACE'/data/mangrove",
    "conversions": {".zip": "shp-to-parquet"},
    "overwrite": true,
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'"
  }' | jq '{target_uri, conversions}'
```

```python
post("/v1/arco/stac-item",
     source_uri=(f"s3://{BUCKET}/{WORKSPACE}/items/mangrove/"
                 f"{WORKSPACE}-mangrove.json"),
     bucket=BUCKET, assets_target=f"{WORKSPACE}/data/mangrove",
     conversions={".zip": "shp-to-parquet"},
     overwrite=True, **S3)
```

Notes:

- Each conversion adds an asset to the Item — `zarr`
  (`application/vnd+zarr`) or `parquet` (`application/vnd.apache.parquet`);
  original assets are kept.
- Auto-conversion: NetCDF assets → `nc-to-zarr`; `.gdb`/`.gdb.zip` →
  `gdb-to-parquet`; `.shp` → `shp-to-parquet`. A plain `.zip` is never
  auto-converted — map it explicitly (`gdb-to-parquet` needs exactly one
  file geodatabase in the archive, `shp-to-parquet` one or more `.shp`
  files; a missing `.gdb` fails with 422 → use `shp-to-parquet`).
- The response's `stage_timings_ms` shows per-stage wall time.

**Next:** [Walkthrough 5 — Collections](walkthrough-5-collections.md)
