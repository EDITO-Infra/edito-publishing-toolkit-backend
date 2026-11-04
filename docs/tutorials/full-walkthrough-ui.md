# Full walkthrough — UI

The same pass in the EPT frontend — no code. Sign in with your EDITO
account; every tool's form *is* the API body. Throughout: `bucket` = your
bucket, `workspace` = `<projectid>-output` (your project's short name,
**must be unique**), and the four S3 fields come from your login. Detail
per step: [walkthroughs 1–7](walkthroughs/walkthrough-1-transform.md).

## Setup

Launch or point at EPT — [Install](../install.md) — open the UI, sign in.

## 1. Transform data

*Transform Data* → **Transform NetCDF to Zarr** — `source_uri` =
`https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc`,
`target` = `<workspace>/data/din`, tick `overwrite`.

*Transform Data* → **Convert ESRI File Geodatabase to GeoParquet** —
`source_uri` =
`https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/e965088b-a265-4517-84df-c49b156af8a7/attachments/EMODnet_HA_Heritage_Shipwrecks_20250409.zip`,
`target` = `<workspace>/data/shipwrecks`, tick `overwrite`.

✅ Check: [NetCDF/Zarr viewer](https://datalab.dive.edito.eu/launcher/service-playground/netcdf-zarr-viewer)
and [Parquet explorer](https://datalab.dive.edito.eu/launcher/service-playground/parquet-explorer)
read your S3 directly.

## 2. A data item

*Metadata Conversion* → **NetCDF to STAC Item** — same `source_uri` as
step 1, `target` = `<workspace>/items/din-auto`, tick `overwrite`, and
**no `item_id`** — EPT proposes one from the file's CF attributes.

*Make STAC* → **Build STAC Item** — copy the response's `stac_item` JSON
as `stac_json`, set `id` = `<projectid>-din`, `target` =
`<workspace>/items/din/<projectid>-din.json`, tick `overwrite`.

## 3. Items from metadata

*Metadata Conversion* → **ISO19139 to STAC Item**, twice:

- **mangrove** — `source_uri` =
  `https://emodnet.ec.europa.eu/geonetwork/srv/api/records/0eeaeb31-c68b-4f1a-93d5-21f8134e5008/formatters/xml`,
  `item_id` = `<projectid>-mangrove`, `target` = `<workspace>/items/mangrove`
- **heritage-shipwrecks** — `source_uri` =
  `https://emodnet.ec.europa.eu/geonetwork/srv/api/records/e965088b-a265-4517-84df-c49b156af8a7/formatters/xml`,
  `item_id` = `<projectid>-heritage-shipwrecks`, `target` =
  `<workspace>/items/heritage-shipwrecks`

Both times: your bucket, S3 fields, `overwrite`.

## 4. ARCO items

*Make STAC* → **ARCO STAC Item**, three times — `source_uri` =
`s3://<bucket>/<workspace>/items/<name>/<projectid>-<name>.json`,
`assets_target` = `<workspace>/data/<name>`, no `item_target` (in place):

- **din** — leave `conversions` empty (`.nc` → zarr, auto-detected)
- **heritage-shipwrecks** — `conversions` = `{".zip": "gdb-to-parquet"}`
- **mangrove** — `conversions` = `{".zip": "shp-to-parquet"}`

✅ Check: the Items now carry `zarr`/`parquet` assets alongside the
originals.

## 5. Collections

First make the workspace folder **public**: EDITO datalab → Storage → the
folder's eyeball icon (toggle back off after the session).

*Make STAC* → **Create Collection Tree**, twice:

- `item_links` = the public URLs of the mangrove + shipwrecks Items,
  `id` = `<projectid>-habitats-and-heritage`, `title` =
  `<projectid> Habitats and Heritage Collection`, `description` = *Demo
  <projectid> Habitats and Heritage Collection for EPT tutorial*,
  `target` = `<workspace>/collections/<projectid>-habitats-and-heritage`
- `item_links` = the DIN Item URL, `id` =
  `<projectid>-ocean-chemistry`, `title` =
  `<projectid> Ocean Chemistry Collection`, `description` = *Demo
  <projectid> Ocean Chemistry Collection for EPT tutorial*,
  `target` = `<workspace>/collections/<projectid>-ocean-chemistry`

*STAC Tools* → **Validate STAC** — each `collection.json` URL,
`validate_children` on. ✅ `stac_is_valid: true`.

## 6. Catalog

*Make STAC* → **Create Catalog Tree** — `collection_links` = the two
`collection.json` URLs, `id` = `<projectid>-catalog`, `title` =
`<projectid> EPT tutorial Catalog`, `description` = *Demo <projectid>
catalog for EPT tutorial*,
`target` = `<workspace>/stac-final`, tick `overwrite`.

*STAC Tools* → **Validate STAC** — `stac-final/catalog.json`,
`validate_children` on. ✅ Open the tree in the STAC browser.

✅ **Done** — data converted, Items built and ARCO-ified, Collections and
Catalog assembled, validated: `stac-final/catalog.json`.

Making it discoverable in the EDITO STAC API is a publication job — one
that **replaces the entire contents** of the catalog it is published into,
so agree with your project before targeting one. Details:
[Publish STAC](../features/publish-stac.md) · questions → EDITO support
or the trainer in a training session.
