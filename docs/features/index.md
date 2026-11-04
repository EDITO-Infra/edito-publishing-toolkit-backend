# Features

EPT is organized as small vertical feature slices. These are the features available in the API.

| Feature | Endpoint | Authentication | Guide |
| --- | --- | --- | --- |
| Validate STAC metadata | `POST /v1/stac/validate` | Access token | [Validate STAC](validate-stac.md) |
| Find on the EDITO STAC API | `GET /v1/stac/find/...` | Access token | [STAC Find](stac-find.md) |
| Fetch one STAC object by URL | `GET /v1/stac/get` | Access token | [STAC Fetch](stac-get.md) |
| Build a STAC Catalog | `POST /v1/stac/build/catalog` | Access token | [STAC Catalog Builder](stac-build-catalog.md) |
| Build a STAC Collection | `POST /v1/stac/build/collection` | Access token | [STAC Collection Builder](stac-build-collection.md) |
| Build a STAC Item | `POST /v1/stac/build/item` | Access token | [STAC Item Builder](stac-build-item.md) |
| Suggest a STAC Catalog from collections | `POST /v1/stac/suggest/catalog` | Access token | [Suggest STAC Catalog](stac-suggest-catalog.md) |
| Suggest a STAC Collection from items | `POST /v1/stac/suggest/collection` | Access token | [Suggest STAC Collection](stac-suggest-collection.md) |
| Assemble a STAC tree from loose JSON files | `POST /v1/stac/assemble` | Access token | [STAC Assemble](assemble-stac.md) |
| Run a feature operation in the background | `POST /v1/runs` | Access token | [EPT job runner](job-runner.md) |
| Get my catalogs | `GET /v1/edito/stac/mycatalogs` | Access token | [Get my catalogs](get-my-catalogs.md) |
| Publish STAC metadata | `POST /v1/edito/stac/publish` | Access token and refresh token | [Publish STAC](publish-stac.md) |
| Remove a STAC catalog | `POST /v1/edito/stac/remove` | Access token and refresh token | [Remove STAC](remove-stac.md) |
| Read publication job status and logs | `GET /v1/edito/publish/jobs/{job_id}` | Access token | [Publication jobs](publication-jobs.md) |
| Upload to S3 | `POST /v1/storage/s3/upload` | Access token | [Upload to S3](upload-s3.md) |
| List my S3 buckets | `POST /v1/storage/s3/buckets` | Access token | [List S3 buckets](list-s3-buckets.md) |
| Transform NetCDF to Zarr | `POST /v1/transform/nc-to-zarr` | Access token | [Transform NetCDF to Zarr](nc-to-zarr.md) |
| Convert GDB to GeoParquet | `POST /v1/transform/gdb-to-parquet` | Access token | [Convert GDB to GeoParquet](gdb-to-parquet.md) |
| Convert Shapefile to GeoParquet | `POST /v1/transform/shp-to-parquet` | Access token | [Convert Shapefile to GeoParquet](shp-to-parquet.md) |
| ISO19139 to STAC Item | `POST /v1/metadata/iso-to-stac-item` | Access token | [ISO19139 to STAC Item](iso-to-stac-item.md) |
| NetCDF metadata to STAC Item | `POST /v1/metadata/netcdf/to-stac-item` | Access token | [NetCDF to STAC Item](netcdf-to-stac-item.md) |
| Zarr metadata to STAC Item | `POST /v1/metadata/zarr/to-stac-item` | Access token | [Zarr to STAC Item](zarr-to-stac-item.md) |
| ARCO-ify a STAC Item's assets | `POST /v1/arco/stac-item` | Access token | [ARCO STAC Item](arco-stac-item.md) |
| Monitoring logs and status | `GET /v1/monitoring/logs`, `GET /v1/monitoring/status` | Access token | [Monitoring](monitoring.md) |

Protected features require an access token from the [token exchange](../auth.md); publishing and removing also send the refresh token.
