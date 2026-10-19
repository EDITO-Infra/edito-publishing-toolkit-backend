# EDITO Publishing Toolkit — tutorials

Tutorial and workshop material for the EDITO Publishing Toolkit (EPT).

## Contents

| Path | What |
| --- | --- |
| `ept-handson-tutorial/` | **EPT hands-on tutorial** — step-by-step walkthrough guides, runnable scripts, and the Marp deck source (repo-only, render locally) |
| `end-to-end-publishing.md` | request-level walkthrough of the full workflow, including publication — kept separate from the deck |
| `ept-handson-tutorial/walkthroughs/` | the seven hands-on walkthrough guides (transform data, items from data, items from metadata, ARCO, collections, catalog, publish) — frontend or `curl`, with viewer checks after every step; walkthrough 1 ships a runnable script (`walkthrough-1-transform.py`) whose commented blocks cover the whole flow |

## Why Marp

Markdown in → HTML/PDF/PPTX out. Same tooling as the EPT docs (mkdocs),
diff-able in git, no vendor lock-in.

## Render

Requires Node 20+:

```bash
cd ept-handson-tutorial
npx -y @marp-team/marp-cli@latest ept-handson-tutorial.marp.md \
  --theme edito2.css --html -o ept-handson-tutorial.html

# or from the repo root
make docs-slides

# PDF / PPTX (needs Chromium; add --browser=download if none installed)
npx -y @marp-team/marp-cli@latest ept-handson-tutorial.marp.md \
  --theme edito2.css --pdf -o ept-handson-tutorial.pdf
```

Or install the **Marp for VS Code** extension and set

```json
"markdown.marp.themes": ["docs/tutorials/ept-handson-tutorial/edito2.css"]
```

for live preview.

## Keeping the deck and the docs in sync

The deck is a snapshot of the running API: the frontend generates its tools
from the OpenAPI schema, and the scenario slides reference real endpoints.
When a feature changes, update both the feature page in `docs/features/` and
the matching scenario slide — then rebuild the HTML and commit both.

The deck is **not served by the mkdocs handbook** — the site serves the
markdown docs (walkthroughs, guides). Render the deck locally when
you need to present (see [Render](#render)); a stale HTML is only a stale
deck, never a stale handbook.
