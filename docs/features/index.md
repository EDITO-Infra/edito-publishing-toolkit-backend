# Features

EPT is organized as small vertical feature slices. These are the features available in the API.

| Feature | Endpoint | Authentication | Guide |
| --- | --- | --- | --- |
| Validate STAC metadata | `POST /v1/stac/validate` | Access token | [Validate STAC](validate-stac.md) |
| Get my catalogs | `GET /v1/edito/stac/mycatalogs` | Access token | [Get my catalogs](get-my-catalogs.md) |
| Publish STAC metadata | `POST /v1/edito/stac/publish` | Access token and refresh token | [Publish STAC](publish-stac.md) |
| Remove a STAC catalog | `POST /v1/edito/stac/remove` | Access token and refresh token | [Remove STAC](remove-stac.md) |
| Read publication job status and logs | `GET /v1/edito/publish/jobs/{job_id}` | Access token | [Publication jobs](publication-jobs.md) |
| Upload to S3 | `POST /v1/storage/s3/upload` | Access token | [Upload to S3](upload-s3.md) |

Protected features require an access token from the [token exchange](../auth.md); publishing and removing also send the refresh token.
