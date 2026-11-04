# EDITO Publishing Toolkit — tutorials

Tutorial material for the EDITO Publishing Toolkit (EPT). The served
handbook (mkdocs site) is the source of truth — no slide decks to keep
in sync.

## Contents

| Path | What |
| --- | --- |
| `index.md` | **Intro** — goals, what EPT is, STAC, ARCO, reading order |
| `session-setup.md` | Run EPT first ([`../install.md`](../install.md)); then the login blocks (python `requests`, curl) and the session variables |
| `walkthroughs/` | The seven step pages — each standalone, frontend + `curl` + python — plus the bash full walkthrough |
| `full-walkthrough-python.md` | The full pass as one runnable script — ends at the validated catalog |
| `full-walkthrough-ui.md` | The full pass in the frontend, checks in the datalab viewers |
| `ept-training-1.md` | Focused path for the training session (shared bucket, publish demoed by the trainer) |
| `workspace/ept-training-session.py` | Runnable training-session script — login + the `post`/`get` helpers from `session-setup.md` |
| `videos/` | Demo recordings, linked from the pages above |

## Archived deck material

The retired slide decks (`*.marp.md`), their theme (`edito.css`) and
screenshots were removed from the repository — not maintained alongside
the docs. If a presentation is needed again, render one from the current
handbook content instead of reviving these.
