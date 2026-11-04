# EDITO Publishing Toolkit

The EDITO Publishing Toolkit (EPT) helps data providers prepare and publish metadata and data products for EDITO.

See the [feature overview](docs/features/index.md) for the currently implemented features.

## Quick start

Run EPT on EDITO with zero install: launch the [service playground](https://datalab.dive.edito.eu/launcher/service-playground/edito-publishing-toolkit-service) from your personal space — no clone, no setup. Sign in with your EDITO account in the UI, or point your scripts at your service URL.

To run EPT yourself, you need [`uv`](https://docs.astral.sh/uv/) (it installs the Python version required by `pyproject.toml` automatically) and a clone:

```bash
git clone https://github.com/EDITO-Infra/edito-publishing-toolkit.git
cd edito-publishing-toolkit
uv sync
uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is then available at `http://localhost:8000`, with interactive documentation at `http://localhost:8000/docs`.

Prefer containers? Prebuilt API and frontend images are on GHCR, no clone needed — see the [Docker usage guide](docs/docker-usage.md). Full prerequisites and options in the [install guide](docs/install.md). See the [usage guide](docs/usage.md) for API usage and hosted deployment status.

## Tutorials

Step-by-step, against real EMODnet data: from ISO19139 records and NetCDF files to a validated STAC catalog on EDITO — python, UI, or bash.

- [Session setup](docs/tutorials/session-setup.md) — get an EPT API running and log in
- [Walkthroughs](docs/tutorials/index.md) — seven focused walkthroughs, plus a full end-to-end guide for [python](docs/tutorials/full-walkthrough-python.md) and the [UI](docs/tutorials/full-walkthrough-ui.md)

## Credentials

Calling protected endpoints requires EDITO credentials. Publishing and removing STAC also require permission to publish within the relevant EDITO project.

If you are unsure which access rights you have, contact [EDITO Support](mailto:support@edito.eu). Also see [Onboard your Data on EDITO](https://help.edito.eu/en/articles/15339950-onboard-your-data-on-edito).

## Documentation

- [Install](docs/install.md) — prerequisites and installing EPT
- [Tutorials](docs/tutorials/index.md) — session setup, walkthroughs, and end-to-end guides
- [Usage](docs/usage.md) — running and using the API, plus hosted deployment status
- [Docker usage](docs/docker-usage.md) — building and running EPT with Docker
- [API](docs/api.md) — endpoints, versioning, asynchronous jobs, and error conventions
- [Authentication](docs/auth.md) — obtaining tokens and authorizing requests
- [Features](docs/features/index.md) — available features and detailed feature guides

Build the documentation site with `make docs-build`, or preview it locally with `make docs-serve` and open `http://127.0.0.1:8001`.

## Suggest a feature

Do you have a feature you would like to see added to EPT? 

[Open an issue or submit a pull request](https://github.com/EDITO-Infra/edito-publishing-toolkit/issues/new) on the EPT GitHub repository.

## Versioning

- v1.0.0
- v1.1.0-beta
- v1.2.0

## License

Funded by the EUropean DIgital Twin Ocean phase 2 project: 
- [https://cordis.europa.eu/project/id/101227771](https://cordis.europa.eu/project/id/101227771)
- [https://doi.org/10.3030/101227771](https://doi.org/10.3030/101227771)
