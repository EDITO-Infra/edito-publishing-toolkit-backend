# Full walkthrough — python

One runnable script: setup once, then steps 1–6 with comments in between.
Detail for every step — the same step in the UI and in bash — is in
[the walkthroughs folder](walkthroughs/walkthrough-1-transform.md).

## Setup — run once

Copy the session block from [Session setup](session-setup.md#log-in) and run
it. Now it's running: `EPT_URL` is set and `post`, `get`, `TOKEN`,
`REFRESH_TOKEN`, `AUTH`, `S3`, `BUCKET`, `PROJECTID`, `WORKSPACE`, `MINIO`
are defined.

## The flow

```python
# == 1. Transform data — the ARCO building blocks on their own ===============

DIN_NC_URL = ("https://s3.waw3-1.cloudferro.com/emodnet/emodnet_geonetwork_assets/"
              "emodnet-chemistry/7f963926-23ba-48d6-ac83-a995ae5ea4aa/"
              "Water_body_dissolved_inorganic_nitrogen.nc")
SHIPWRECKS_ZIP_URL = ("https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/"
                      "e965088b-a265-4517-84df-c49b156af8a7/attachments/"
                      "EMODnet_HA_Heritage_Shipwrecks_20250409.zip")

# NetCDF → Zarr: chunk-readable, consolidated, under <workspace>/data/
out = post("/v1/conversions/netcdf/zarr",
           source_uri=DIN_NC_URL, bucket=BUCKET,
           target=f"{WORKSPACE}/data/din",
           consolidated=True, overwrite=True, **S3)
pprint(out)

# GDB zip → GeoParquet: the zip holds exactly one file geodatabase —
# one Parquet file per layer. Check both in the datalab viewers.
out = post("/v1/conversions/gdb/parquet",
           source_uri=SHIPWRECKS_ZIP_URL, bucket=BUCKET,
           target=f"{WORKSPACE}/data/shipwrecks", overwrite=True, **S3)
pprint(out)

# == 2. A data item — suggest → make it yours ==============================

# no item_id: EPT proposes one from the file's own CF attributes
item = post("/v1/conversions/netcdf/stac-item",
            source_uri=DIN_NC_URL, bucket=BUCKET,
            target=f"{WORKSPACE}/items/din-auto", overwrite=True, **S3)
print("proposed id:", item["stac_item"]["id"])

# the response already carries the full Item — set your project-scoped id
# and rebuild at the exact path the later steps read
out = post("/v1/stac/objects/item",
     stac_json=item["stac_item"], id=f"{PROJECTID}-din",
     bucket=BUCKET, target=f"{WORKSPACE}/items/din/{PROJECTID}-din.json",
     overwrite=True, **S3)
pprint(out)

# == 3. Items from metadata — ISO19139 records → STAC Items ================

RECORDS = {"mangrove": "0eeaeb31-c68b-4f1a-93d5-21f8134e5008",
           "heritage-shipwrecks": "e965088b-a265-4517-84df-c49b156af8a7"}
for name, uuid in RECORDS.items():
    pprint(post("/v1/conversions/iso/stac-item",
               source_uri=(f"https://emodnet.ec.europa.eu/geonetwork/srv/api/"
                           f"records/{uuid}/formatters/xml"),
               item_id=f"{PROJECTID}-{name}",      # project-scoped, predictable
               bucket=BUCKET, target=f"{WORKSPACE}/items/{name}",
               overwrite=True, **S3))

# == 4. ARCO items — convert the Items' data assets in place ===============

# {} = auto-detect from the asset format; a plain .zip is never
# auto-converted — map it explicitly. No item_target → in place.
def arco(name, conversions):
    out = post("/v1/conversions/stac-item/arco",
               source_uri=f"s3://{BUCKET}/{WORKSPACE}/items/{name}/{PROJECTID}-{name}.json",
               bucket=BUCKET, assets_target=f"{WORKSPACE}/data/{name}",
               conversions=conversions, overwrite=True, **S3)
    pprint(out)

arco("din", {})                                   # .nc → zarr
arco("heritage-shipwrecks", {".zip": "gdb-to-parquet"})
arco("mangrove", {".zip": "shp-to-parquet"})      # shapefile zip, no .gdb

# == 5. Collections — Items → self-contained Collection folders ============
# the workspace folder must be PUBLIC from here on
# (EDITO datalab → Storage → eyeball icon)

def build_collection(collection_id, title, description, item_names):
    """EPT derives extent/license/keywords/links from the Items; the export
    stamps the id onto item copies — the folder is complete on its own."""
    out = post("/v1/stac/collections/tree",
               item_links=[f"{MINIO}/{BUCKET}/{WORKSPACE}/items/{n}/{PROJECTID}-{n}.json"
                           for n in item_names],
               id=collection_id, title=title, description=description,
               bucket=BUCKET,
               target=f"{WORKSPACE}/collections/{collection_id}",
               overwrite=True, **S3)
    pprint(out)

def validate(url):
    out = post("/v1/stac/validations", stac_url=url, validate_children=True)
    pprint(out)

habitats_id = f"{PROJECTID}-habitats-and-heritage"
ocean_id = f"{PROJECTID}-ocean-chemistry"
build_collection(habitats_id, f"{PROJECTID} Habitats and Heritage Collection",
                 f"Demo {PROJECTID} Habitats and Heritage Collection for EPT "
                 "tutorial.",
                 ["mangrove", "heritage-shipwrecks"])
build_collection(ocean_id, f"{PROJECTID} Ocean Chemistry Collection",
                 f"Demo {PROJECTID} Ocean Chemistry Collection for EPT tutorial.",
                 ["din"])
validate(f"{MINIO}/{BUCKET}/{WORKSPACE}/collections/{habitats_id}/collection.json")
validate(f"{MINIO}/{BUCKET}/{WORKSPACE}/collections/{ocean_id}/collection.json")

# == 6. Catalog — Collections → the final validated tree ===================

catalog = post("/v1/stac/catalogs/tree",
               collection_links=[f"{MINIO}/{BUCKET}/{WORKSPACE}/collections/{cid}/collection.json"
                                 for cid in (habitats_id, ocean_id)],
               id=f"{PROJECTID}-catalog",
               title=f"{PROJECTID} EPT tutorial Catalog",
               description=f"Demo {PROJECTID} catalog for EPT tutorial.",
               bucket=BUCKET, target=f"{WORKSPACE}/stac-final",
               overwrite=True, **S3)
pprint(catalog)

CATALOG_URL = f"{MINIO}/{BUCKET}/{WORKSPACE}/stac-final/catalog.json"
validate(CATALOG_URL)
```

✅ **Done** — data converted, Items built and ARCO-ified, Collections and
Catalog assembled, validated: `stac-final/catalog.json`.

Making it discoverable in the EDITO STAC API is a publication job — one
that **replaces the entire contents** of the catalog it is published into,
so agree with your project before targeting one. Details:
[Publish STAC](../features/publish-stac.md) · questions → EDITO support
or the trainer in a training session.
