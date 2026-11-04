# EPT tutorials

From metadata records and datasets to a validated STAC catalog on
EDITO — against real EMODnet data.

## The goals

1. **Make STAC** — your existing ISO19139 records and data files become
   valid STAC Items, wrapped in Collections and a Catalog, hosted on S3
   and discoverable through EDITO.
2. **Make ARCO** — the same data converted to cloud-optimized formats
   (NetCDF → Zarr, GDB/shapefile → GeoParquet), locally or on EDITO, so
   clients read chunks over HTTP instead of downloading whole files.

Both run through one EPT API — python, UI, or bash.

## What is EPT

**One API that does the publishing work**:

| Convert | Validate | Deliver |
| --- | --- | --- |
| ISO19139 → STAC · NetCDF → Zarr · GDB → GeoParquet | clean in, valid STAC out — checked against STAC + EDITO rules | local paths or S3 — private or public data |

An optional **frontend** ships with it: every capability is a tool
generated live from the OpenAPI schema — fill the form, press *Run Tool*,
inspect the JSON response. EPT runs **local or in the cloud**: as the
as the EDITO service (zero install), from docker images, or from a clone — same
API, same contract ([Install](../install.md)). Under the hood it uses EDITO
services (auth, S3 storage, STAC API, publication); what you code against
is just EPT.

## [STAC](https://stacspec.org)

```text
Catalog                         ← root, ties it together
└── Collection                  ← shared extents, license
    ├── Item                    ← one dataset, one place, one time
    │   ├── Asset  Zarr         ← the actual data
    │   └── Asset  ISO19139     ← the metadata record
    └── Item
```

Plain JSON with `links` — a linked file structure you can host as
static files on S3 (crawlable, cacheable, versionable) or serve from a
[STAC API](https://github.com/radiantearth/stac-api-spec) — typically an
[OGC API — Features](https://ogcapi.ogc.org/features/) implementation,
backed by a database or by those same static files. The [EDITO STAC
API](https://api.dive.edito.eu/data) is one such API, and STAC is what it
serves and searches — so your data needs a STAC representation to be
found.
Your ISO19139 records already hold everything STAC needs — bbox, temporal
extent, contacts, constraints. EPT does the mapping: one call per Item,
one call per Collection, one call for the Catalog.

## ARCO — cloud-optimized data

ARCO (*analysis-ready, cloud-optimized*) is about how data is stored, not
what it contains: cut into chunks a client fetches individually over
HTTP, instead of downloading whole files first. Currently, the two ARCO formats EPT
produces:

- **[Zarr](https://zarr.dev)** — an N-dimensional array store: the array
  is split into chunks, each chunk a separate object on S3 with plain
  JSON metadata describing shape, dtype and compression. Reading one
  time slice means fetching only that slice's chunks — not the whole
  NetCDF. `xarray.open_zarr` reassembles it into the familiar labelled
  dataset, CF metadata intact. Works for gridded/raster data and time
  series.
- **[GeoParquet](https://geoparquet.org)** — Parquet with a standard
  geometry column: a columnar table where data is grouped into compressed
  row groups. A query on two columns reads only those columns, and a
  spatial or attribute filter can skip whole row groups without reading
  them. Works for vector tables — points, lines, polygons.

Both live as ordinary objects on your EDITO S3 storage — the same place
the STAC Assets link to — so any HTTP client, notebook or GIS can read
them without special data infrastructure. Conversion is format-only: no
content checks or coordinate transformations; those belong upstream
of EPT.

## Getting Started

- [Install](../install.md) — run EPT on EDITO (zero install), docker or
  clone; then UI or point your script at `EPT_URL`
- [Session setup](session-setup.md) — log in once; defines the variables
  and helpers every walkthrough uses
- **Walkthroughs** — the workflow step by step, each standalone:
  - [1 — Transform data](walkthroughs/walkthrough-1-transform.md)
  - [2 — A data item](walkthroughs/walkthrough-2-data-items.md)
  - [3 — Items from metadata](walkthroughs/walkthrough-3-metadata-items.md)
  - [4 — ARCO items](walkthroughs/walkthrough-4-arco.md)
  - [5 — Collections](walkthroughs/walkthrough-5-collections.md)
  - [6 — Catalog](walkthroughs/walkthrough-6-catalog.md)
  - [7 — Publish](walkthroughs/walkthrough-7-publish.md)
- The same workflow as one pass, if you prefer: [full walkthrough —
  python](full-walkthrough-python.md) · [full walkthrough —
  UI](full-walkthrough-ui.md) · [full walkthrough —
  bash](walkthroughs/full-walkthrough-bash.md)
- [Training 05-11-2026](ept-training-1.md) — shared-bucket session;
  the trainer publishes one final project STAC

> A published catalog is visible to your project, not necessarily public —
> wider visibility is a request to EDITO support.
