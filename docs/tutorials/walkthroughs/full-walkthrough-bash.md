# Full walkthrough — bash

The same pass in `curl` + `jq` — setup once, then steps 1–6. Needs a POSIX
shell (`bash`): on Windows, Git Bash or WSL. Detail per
step: the walkthrough pages in this folder.

## Setup — run once

Copy the login block from [Session setup](../session-setup.md#log-in) and run
it. Now it's running: `$EPT_URL`, `$TOKEN`, `$REFRESH_TOKEN`, `$S3_KEY`,
`$S3_SECRET`, `$S3_SESSION`, `$BUCKET`, `$PROJECTID`, `$WORKSPACE` are
set.

## 1. Transform data

```bash
# NetCDF → Zarr
curl -s -X POST $EPT_URL/v1/conversions/netcdf/zarr \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
    "source_uri": "https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/data/din",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "consolidated": true, "overwrite": true }' | jq .target_uri

# GDB zip → GeoParquet — one Parquet file per layer
curl -s -X POST $EPT_URL/v1/conversions/gdb/parquet \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
    "source_uri": "https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/e965088b-a265-4517-84df-c49b156af8a7/attachments/EMODnet_HA_Heritage_Shipwrecks_20250409.zip",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/data/shipwrecks",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true }' | jq .layers
```

✅ Check: the datalab NetCDF/Zarr viewer and Parquet explorer.

## 2. A data item — suggest → make it yours

```bash
# no item_id: EPT proposes one from the file's own CF attributes
curl -s -X POST $EPT_URL/v1/conversions/netcdf/stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
    "source_uri": "https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/Water_body_dissolved_inorganic_nitrogen.nc",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/items/din-auto",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true }' | tee item-auto.json | jq '{id: .stac_item.id, bbox: .stac_item.bbox}'

# the response already carries the full Item — set your project-scoped id
jq '.stac_item' item-auto.json > din-auto-item.json

curl -s -X POST $EPT_URL/v1/stac/objects/item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --slurpfile i din-auto-item.json \
        --arg bucket "$BUCKET" --arg target "$WORKSPACE/items/din/$PROJECTID-din.json" \
        --arg endpoint "https://minio.dive.edito.eu" \
        --arg key "$S3_KEY" --arg secret "$S3_SECRET" --arg token "$S3_SESSION" \
        '{stac_json: $i[0], id: "'$PROJECTID'-din", bucket: $bucket, target: $target,
          endpoint_url: $endpoint, access_key_id: $key,
          secret_access_key: $secret, session_token: $token, overwrite: true}')" \
  | jq .target_uri
rm item-auto.json din-auto-item.json
```

## 3. Items from metadata — ISO19139 → STAC Items

```bash
for pair in "mangrove:0eeaeb31-c68b-4f1a-93d5-21f8134e5008" \
            "heritage-shipwrecks:e965088b-a265-4517-84df-c49b156af8a7"; do
  name=${pair%%:*}; uuid=${pair#*:}
  curl -s -X POST $EPT_URL/v1/conversions/iso/stac-item \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{
      \"source_uri\": \"https://emodnet.ec.europa.eu/geonetwork/srv/api/records/$uuid/formatters/xml\",
      \"item_id\": \"$PROJECTID-$name\",
      \"bucket\": \"$BUCKET\", \"target\": \"$WORKSPACE/items/$name\",
      \"endpoint_url\": \"https://minio.dive.edito.eu\",
      \"access_key_id\": \"$S3_KEY\", \"secret_access_key\": \"$S3_SECRET\",
      \"session_token\": \"$S3_SESSION\", \"overwrite\": true }" | jq .target_uri
done
```

## 4. ARCO items — convert the Items' data assets in place

```bash
# {} = auto-detect from the asset format; a plain .zip is never
# auto-converted — map it explicitly. No item_target → in place.
for spec in "din:{}" \
            "heritage-shipwrecks:{\".zip\":\"gdb-to-parquet\"}" \
            "mangrove:{\".zip\":\"shp-to-parquet\"}"; do
  name=${spec%%:*}; conversions=${spec#*:}
  curl -s -X POST $EPT_URL/v1/conversions/stac-item/arco \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{
      \"source_uri\": \"s3://$BUCKET/$WORKSPACE/items/$name/$PROJECTID-$name.json\",
      \"bucket\": \"$BUCKET\", \"assets_target\": \"$WORKSPACE/data/$name\",
      \"conversions\": $conversions,
      \"endpoint_url\": \"https://minio.dive.edito.eu\",
      \"access_key_id\": \"$S3_KEY\", \"secret_access_key\": \"$S3_SECRET\",
      \"session_token\": \"$S3_SESSION\", \"overwrite\": true }" | jq '{target_uri, conversions}'
done
```

## 5. Collections — Items → self-contained Collection folders

First make the workspace folder **public** (EDITO datalab → Storage →
eyeball icon).

```bash
collections_tree() {   # collections_tree <id> <title> <item...>
  local cid=$1 title=$2; shift 2
  local links="[" item
  for item in "$@"; do
    links="$links\"https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/items/$item/$PROJECTID-$item.json\","
  done
  curl -s -X POST $EPT_URL/v1/stac/collections/tree \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{
      \"item_links\": ${links%,}],
      \"id\": \"$cid\", \"title\": \"$title\",
      \"description\": \"Demo $title for EPT tutorial.\",
      \"bucket\": \"$BUCKET\",
      \"target\": \"$WORKSPACE/collections/$cid\",
      \"endpoint_url\": \"https://minio.dive.edito.eu\",
      \"access_key_id\": \"$S3_KEY\", \"secret_access_key\": \"$S3_SECRET\",
      \"session_token\": \"$S3_SESSION\", \"overwrite\": true }" \
    | jq '{status, exported, collection_uri, warnings}'
}

habitats_id=$PROJECTID-habitats-and-heritage
ocean_id=$PROJECTID-ocean-chemistry
collections_tree $habitats_id "$PROJECTID Habitats and Heritage Collection" mangrove heritage-shipwrecks
collections_tree $ocean_id "$PROJECTID Ocean Chemistry Collection" din
```

Validate each folder where it stands:

```bash
for cid in $habitats_id $ocean_id; do
  curl -s -X POST $EPT_URL/v1/stac/validations \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"stac_url\": \"https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/collections/$cid/collection.json\",
         \"validate_children\": true}" | jq '{stac_is_valid, summary}'
done
```

## 6. Catalog — Collections → the final validated tree

```bash
curl -s -X POST $EPT_URL/v1/stac/catalogs/tree \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
    "collection_links": [
      "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/collections/'$habitats_id'/collection.json",
      "https://minio.dive.edito.eu/'$BUCKET'/'$WORKSPACE'/collections/'$ocean_id'/collection.json"
    ],
    "id": "'$PROJECTID'-catalog",
    "title": "'$PROJECTID' EPT tutorial Catalog",
    "description": "Demo '$PROJECTID' catalog for EPT tutorial.",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/stac-final",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true }' | jq '{status, exported, catalog_uri, warnings}'

CATALOG_URL="https://minio.dive.edito.eu/$BUCKET/$WORKSPACE/stac-final/catalog.json"

curl -s -X POST $EPT_URL/v1/stac/validations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"stac_url\": \"$CATALOG_URL\", \"validate_children\": true}" \
  | jq '{stac_is_valid, summary}'
```

✅ **Done** — data converted, Items built and ARCO-ified, Collections and
Catalog assembled, validated: `stac-final/catalog.json`.

Making it discoverable in the EDITO STAC API is a publication job — one
that **replaces the entire contents** of the catalog it is published into,
so agree with your project before targeting one. Details:
[Publish STAC](../../features/publish-stac.md) · questions → EDITO support
or the trainer in a training session.
