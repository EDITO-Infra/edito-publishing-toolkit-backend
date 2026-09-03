# EDITO Publishing Toolkit

The EDITO Publishing Toolkit (EPT) helps data providers prepare and publish metadata and data products for EDITO.

See the [feature overview](docs/features/index.md) for the currently implemented features.

## Quick start

EPT requires Python 3.13 or newer and uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is then available at `http://localhost:8000`, with interactive documentation at `http://localhost:8000/docs`.

For container-based usage, see the [Docker usage guide](docs/docker-usage.md). See the [usage guide](docs/usage.md) for API usage and hosted deployment status.

## Credentials

Calling protected endpoints requires EDITO credentials. Publishing and removing STAC also require permission to publish within the relevant EDITO project.

If you are unsure which access rights you have, contact [EDITO Support](mailto:support@edito.eu). Also see [Onboard your Data on EDITO](https://help.edito.eu/en/articles/15339950-onboard-your-data-on-edito).

## Documentation

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

## License

Funded by the EUropean DIgital Twin Ocean phase 2 project: 
- [https://cordis.europa.eu/project/id/101227771](https://cordis.europa.eu/project/id/101227771)
- [https://doi.org/10.3030/101227771](https://doi.org/10.3030/101227771)
