# Walkthrough 1 — Transform data

Two standalone conversions — the ARCO building blocks on their own:

1. **NetCDF → Zarr** — EMODnet Chemistry dissolved inorganic nitrogen
2. **GDB zip → GeoParquet** — EMODnet Heritage shipwrecks

Everything converted lands under `<workspace>/data/` — check the outputs in
the viewers.

Works with the EPT frontend or `curl`/`python` — every step shows both.

> **Before running:** get an EPT API and run the **session setup in the
> [index](../index.md)** (bash *or* python — one of the two) — it provides
> `TOKEN`, the four S3 credentials, `$BUCKET`/`$WORKSPACE` and (python) the
> `post()` helper used below; the source URLs are defined in the python
> blocks of this page. Reading and writing works with the workspace folder
> public or private.

## 1. NetCDF → Zarr

**In the frontend:** *Transform Data* → **Transform NetCDF to Zarr** —
`source_uri` =
`https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc`,
`bucket` = your bucket, `target` = `<workspace>/data/din`, and the four S3
fields from your login; tick `overwrite`.

`POST /v1/transform/nc-to-zarr`: the EMODnet Chemistry dissolved-inorganic-
nitrogen analysis becomes a chunk-readable Zarr store, named after the source
without its `.nc` extension:

```bash
curl -X POST $EPT_URL/v1/transform/nc-to-zarr \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/data/din",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "consolidated": true, "overwrite": true
  }' | jq .target_uri
```

```python
# needs the index session setup: post(), EPT, TOKEN, S3, BUCKET, WORKSPACE
DIN_NC_URL = ("https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/"
              "emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/"
              "Water_body_dissolved_inorganic_nitrogen.nc")

post("/v1/transform/nc-to-zarr",                # POST {EPT}/v1/transform/nc-to-zarr
     source_uri=DIN_NC_URL,                     # remote NetCDF to convert
     bucket=BUCKET,                             # S3 bucket written into
     target=f"{WORKSPACE}/data/din",            # prefix inside the bucket
     consolidated=True, overwrite=True,
     **S3)                                      # endpoint_url + the 3 S3 credential fields
```

`post(path, **body)` (from the index setup) sends the keywords as the JSON
body to `EPT + path` with the bearer-token header, and returns the response
JSON. The matching `curl` block above shows the same body in full.

Inspect the store in the
[NetCDF/Zarr viewer](https://datalab.dive.edito.eu/launcher/service-playground/netcdf-zarr-viewer)
— variables, chunks, time axis.

## 2. GDB zip → GeoParquet

**In the frontend:** *Transform Data* → **Convert ESRI File Geodatabase to
GeoParquet** — `source_uri` =
`https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/e965088b-a265-4517-84df-c49b156af8a7/attachments/EMODnet_HA_Heritage_Shipwrecks_20250409.zip`,
`bucket` = your bucket, `target_uri` = `s3://<bucket>/<workspace>/data/shipwrecks`,
same S3 fields; tick `overwrite`.

`POST /v1/transform/gdb-to-parquet`: the EMODnet Heritage shipwrecks
download is a zip containing exactly one file geodatabase — EPT stages the
remote zip itself (no local unzip, no reprojection), one Parquet file per
layer:

```bash
curl -X POST $EPT_URL/v1/transform/gdb-to-parquet \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/e965088b-a265-4517-84df-c49b156af8a7/attachments/EMODnet_HA_Heritage_Shipwrecks_20250409.zip",
    "bucket": "'$BUCKET'"
    "target": "'$WORKSPACE'/data/shipwrecks",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true
  }' | jq .layers
```

```python
SHIPWRECKS_ZIP_URL = ("https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/"
                      "e965088b-a265-4517-84df-c49b156af8a7/attachments/"
                      "EMODnet_HA_Heritage_Shipwrecks_20250409.zip")
post("/v1/transform/gdb-to-parquet",
     source_uri=SHIPWRECKS_ZIP_URL,   # the remote gdb zip defined above
     target=f"{WORKSPACE}/data/shipwrecks",
     bucket=BUCKET,
     overwrite=True, **S3)
```

Inspect the layers in the
[Parquet explorer](https://datalab.dive.edito.eu/launcher/service-playground/parquet-explorer)
— schema, geometry types, row counts.

**Next:** [Walkthrough 2 — A data item](walkthrough-2-data-items.md)
