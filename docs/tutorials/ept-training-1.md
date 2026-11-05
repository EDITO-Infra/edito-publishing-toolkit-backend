# Hands-on session — EPT Training 1

From metadata records and NetCDF files to a validated STAC catalog on
EDITO — against real EMODnet data.

[EPT Tutorials Overview](index.md) · [Install Guide](../install.md)

## What you'll build

Your data becomes a **STAC catalog** — plain JSON files on S3:

```text
Catalog                         ← root, ties it together
└── Collection                  ← shared extents, license
    ├── Item                    ← one dataset, one place, one time
    │   ├── Asset  Zarr         ← the actual data
    │   └── Asset  ISO19139     ← the metadata record
    └── Item
```

Items group into **Collections** (shared extent and
license); Collections tie together under a **Catalog**, the root
everything hangs off. It's all JSON with `links`, hosted as static
files on S3.  Valid STAC can be published to the [EDITO STAC API](https://api.dive.edito.eu/data) and 
it is viewable in the [STAC viewer](https://viewer.dive.edito.eu/).
EPT does the mapping: one call per Item, one per Collection, one for
the Catalog.

Alongside, the data itself is converted to **ARCO** — *analysis-ready,
cloud-optimized* — formats, so clients read chunks over HTTP instead of
downloading whole files:

- **Zarr** (from NetCDF) — the array is split into chunks, each chunk
  its own object on S3 with small JSON metadata describing shape, dtype
  and compression. Reading one time slice fetches only that slice's
  chunks, not the whole NetCDF; `xarray.open_zarr` reassembles it into
  the familiar labelled dataset, CF metadata intact. For gridded/raster
  data and time series.
- **GeoParquet** (from GDB/shapefile) — Parquet with a standard geometry
  column: a columnar table grouped into compressed row groups. A query
  on two columns reads only those columns; a spatial or attribute filter
  skips whole row groups without reading them. For vector tables —
  points, lines, polygons.

EPT does all of this through one API — UI, curl, or python. Full
background: [the tutorials intro](index.md).

## Setup

From **your personal space**:

- **Run EPT** — today on EDITO, zero install: the
   [edito-publishing-toolkit-service](https://datalab.dive.edito.eu/launcher/service-playground/edito-publishing-toolkit-service)
   playground · 🎬 [video](videos/launch-ept-edito-service.mp4). The
   other option — running the backend yourself (docker or clone) — is
   the same API at the same endpoints; try it later, see the
   [Install Guide](../install.md).
- **Your service URL** — the address bar while your service is open,
   or datalab → **My services** → click your service. It looks like
   `https://user-<your-username>-…-0.lab.dive.edito.eu/api/` — that
   whole link, `/api/` included, is your `EPT_URL`. 
- **Side services for checking your work** (optional, launch now, use
   later):
   [STAC browser](https://browser.moregeo.it/) — paste a
   catalog/Collection/Item URL and browse the tree;
   [Parquet explorer](https://datalab.dive.edito.eu/launcher/service-playground/parquet-explorer) —
   inspect the GeoParquet the conversions produce.

**Today you work in a shared project bucket** (`project-ept-training1`).
Pick a unique handle — your EDITO username is easiest; avoid
`project1`/`test`. Every id and folder derives from it
(`<handle>-output/…`, `<handle>-din`, `<handle>-catalog`), so parallel
sessions in the shared bucket never collide.

Your deliverable: **a validated catalog** referencing cloud-optimized
data in your output folder.

## How we'll work

The walkthroughs we'll be going through:

| Step | Page | Done when |
| --- | --- | --- |
| 1 — Transform data | [walkthrough 1](walkthroughs/walkthrough-1-transform.md) | Zarr + GeoParquet under `<handle>-output/data/`, checked in the viewers |
| 2 — A data item | [walkthrough 2](walkthroughs/walkthrough-2-data-items.md) | `<handle>-din.json` in `<handle>-output/items/din/` |
| 3 — Items from metadata | [walkthrough 3](walkthroughs/walkthrough-3-metadata-items.md) | `<handle>-mangrove`, `<handle>-heritage-shipwrecks` items |
| 4 — ARCO items | [walkthrough 4](walkthroughs/walkthrough-4-arco.md) | every Item carries cloud-optimized assets |
| 5 — Collections | [walkthrough 5](walkthroughs/walkthrough-5-collections.md) | two validated Collection folders |
| 6 — Catalog | [walkthrough 6](walkthroughs/walkthrough-6-catalog.md) | schema-valid `<handle>-output/stac-final/` — paste it into the STAC browser |

**In the frontend.** Open your service URL and sign in —
every EPT capability is a tool form, *Run Tool* submits it. There is a
**Logs** tab so you can follow
what calls are being made and what it's doing.

**Also in a jupyter notebook.** Launch a [**Jupyter notebook
service**](https://datalab.dive.edito.eu/launcher/ide/jupyter-python)
and create a new Python notebook. (Running EPT locally? Then you need a
local notebook/script instead.) First cell, the session values:

```python
import os
os.environ["EPT_URL"] = "<your service url>"        # the /api/ link of your service — see Setup above
os.environ["EPT_BUCKET"] = "project-ept-training1"  # today's shared bucket
os.environ["EPT_PROJECTID"] = "<your-handle>"       # your unique handle
```

Second cell, the session setup block from
[Session setup](session-setup.md#log-in) (the python one): it
authenticates, mints your S3 credentials and defines the `post`/`get`
helpers — fill in your EDITO username and password when it prompts
(prefer the env-file way? that page shows it too).

From there, each walkthrough's python block is a cell — paste and run
in order. While they run, datalab → **My services** → your service →
**Logs** tab shows every call, whichever client made it.

**Convert your own data.** The calls only care about a `source_uri`.
Have your own NetCDF or ISO19139 record? Use its URL — public
`https://…`, or a file you've put in the bucket (`s3://<bucket>/…`) —
in place of the EMODnet one in the step-2 or step-3 cell, with your own
`item_id`. Add it to a Collection's `item_links` in the step-5 cell and
re-run steps 5–6 — it lands in your final catalog like any other Item.

**One pass instead?** The same six steps, one runnable script:
[Full walkthrough — python](full-walkthrough-python.md).

## Publishing — by the session leader

Note: publishing replaces the target project catalog entirely — a STAC
published into `projects/ept-training1` *is* the `ept-training1` catalog.

So during this training, don't publish into the shared project catalog
yourself. At the end of the session the session leader will assemble a
single collaborative STAC from everyone's **valid** catalogs and publish
that to `projects/ept-training1`.

If you later publish data on EDITO under a project you own or join, you
use the same tools yourself, following
[Walkthrough 7 — Publish](walkthroughs/walkthrough-7-publish.md).
