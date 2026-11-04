# Hands-on session - EPT Training 1

From metadata records and NetCDF files to a validated STAC catalog on
EDITO — against real EMODnet data.

[EPT Tutorials Overview](index.md)

[Install Guide](../install.md) 

## Today

You work in a **shared project bucket** (`project-ept-training1`).

- **Pick a unique handle** — your EDITO username is easiest; avoid
  `project1`/`test`. Every id and folder derives from it
  (`<handle>-output/…`, `<handle>-din`, `<handle>-catalog`), so parallel
  sessions in the shared bucket never collide.
- Your deliverable is **a validated catalog** that references Cloud
  Optimized data in your output folder.

## Setup

- Launch EPT from **your personal space** (avoids concurrent launches on
   the project quota):
   [edito-publishing-toolkit-service](https://datalab.dive.edito.eu/launcher/service-playground/edito-publishing-toolkit-service)
   · 🎬 [video](videos/launch-ept-edito-service.mp4)
- Optional — also launch the
   [Parquet Explorer](https://datalab.dive.edito.eu/launcher/service-playground/parquet-explorer)
   from your personal space. Handy later, when the walkthroughs produce
   GeoParquet, to inspect the conversions.
- Pick how you work — both use the same API:
   - **UI only** — open your service URL and sign in with your EDITO
     account. Follow the walkthroughs' UI blocks; every step is a form
     in the frontend, no scripting needed.
   - **Scripts** — fill in the [env file](session-setup.md#log-in) once
     (`cp env.example .env`, then `set -a && source .env && set +a`),
     and run the code blocks in each walkthrough page as-is. Filled in
     for this session:

     ```bash
     EDITO_USERNAME=your-edito-username
     EDITO_PASSWORD=your-edito-password
     EPT_URL=<your service url>
     EPT_BUCKET=project-ept-training1
     EPT_PROJECTID=<your-handle>
     ```

     The walkthroughs then run as-is: each step shows the same call as
     UI (`curl` + python blocks).

## The path — walkthroughs 1–6

| Step | Page | Done when |
| --- | --- | --- |
| 1 | [Transform data](walkthroughs/walkthrough-1-transform.md) | Zarr + GeoParquet under `<handle>-output/data/`, checked in the viewers |
| 2 | [A data item](walkthroughs/walkthrough-2-data-items.md) | `<handle>-din.json` in `<handle>-output/items/din/` |
| 3 | [Items from metadata](walkthroughs/walkthrough-3-metadata-items.md) | ISO records → `<handle>-mangrove`, `<handle>-heritage-shipwrecks` items |
| 4 | [ARCO items](walkthroughs/walkthrough-4-arco.md) | every Item carries cloud-optimized assets |
| 5 | [Collections](walkthroughs/walkthrough-5-collections.md) — workspace folder **public** first | two validated Collection folders |
| 6 | [Catalog](walkthroughs/walkthrough-6-catalog.md) | schema-valid `<handle>-output/stac-final/` — open it in the [STAC browser](https://browser.moregeo.it/) |

🎬 Every step's walkthrough page carries a screencast of that step — a
stand-in when a live step can't run, skippable otherwise. The
walkthrough pages remain the reference for every field.

## Full walkthroughs — the same flow in one pass

Prefer one runnable script over six pages? Same steps, same order:

- [Full walkthrough — python](full-walkthrough-python.md)
- [Full walkthrough — UI](full-walkthrough-ui.md)
- [Full walkthrough — bash](walkthroughs/full-walkthrough-bash.md)

Use your `<handle>` everywhere these say `<projectid>` — and remember
the bucket is `project-ept-training1`, not your own.

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
