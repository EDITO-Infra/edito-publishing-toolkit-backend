# Features

EPT is organized as small vertical feature slices. This page lists the features currently registered in the API.

## Available API features

| Feature | Endpoint | Authentication | Guide |
| --- | --- | --- | --- |
| Get my catalogs | `GET /v1/edito/stac/mycatalogs` | Access token | [Get my catalogs](get-my-catalogs.md) |
| Publish STAC metadata | `POST /v1/edito/stac/publish` | Access token and refresh token | [Publish STAC](publish-stac.md) |
| Remove a STAC catalog | `POST /v1/edito/stac/remove` | Access token and refresh token | [Remove STAC](remove-stac.md) |
| Read publication job status and logs | `GET /v1/edito/publish/jobs/{job_id}` | Access token | [Publication jobs](publication-jobs.md) |

EPT also provides a public token exchange at `POST /v1/auth`. See [Authentication](../auth.md) for the supported grants and required headers.


## Feature metadata and documentation

Each implemented feature has a manifest at `ept/features/<feature_name>/feature.toml`. The manifest declares registry metadata such as its stable key, release, entrypoint, routes, dependencies, and test locations.

User-facing examples belong in `docs/features/`. Implementation details remain with the corresponding feature slice under `ept/features/`.

## Suggest a feature

Do you have a feature you would like to see added to EPT? 

[Open an issue or submit a pull request](https://github.com/EDITO-Infra/edito-publishing-toolkit/issues/new) on the EPT GitHub repository.
