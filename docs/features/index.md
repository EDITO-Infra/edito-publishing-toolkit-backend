# Features

EPT is organized as small vertical feature slices. These are the features available in the API.

| Feature | Endpoint | Authentication | Guide |
| --- | --- | --- | --- |
| Validate STAC metadata | `POST /v1/stac/validations` | Access token | [Validate STAC](validate-stac.md) |
| Find on the EDITO STAC API | `GET /v1/stac/...` | Access token | [STAC Find](stac-find.md) |
| Fetch one STAC object by URL | `GET /v1/stac/objects` | Access token | [STAC Fetch](stac-get.md) |
| Build a STAC Catalog | `POST /v1/stac/objects/catalog` | Access token | [STAC Catalog Builder](stac-build-catalog.md) |
| Build a STAC Collection | `POST /v1/stac/objects/collection` | Access token | [STAC Collection Builder](stac-build-collection.md) |
| Build / edit a STAC Item | `POST` / `PUT /v1/stac/objects/item` | Access token | [STAC Item Builder](stac-build-item.md) |
| Create a Collection tree from items | `POST /v1/stac/collections/tree` | Access token | [Create Collection Tree](create-collection-tree.md) |
| Create a Catalog tree from collections and catalogs | `POST /v1/stac/catalogs/tree` | Access token | [Create Catalog Tree](create-catalog-tree.md) |
| Run a feature operation in the background | `POST /v1/runs` | Access token | [EPT job runner](job-runner.md) |
| Get my catalogs | `GET /v1/edito/stac/mycatalogs` | Access token | [Get my catalogs](get-my-catalogs.md) |
| Publish STAC metadata | `POST /v1/edito/stac/jobs/publication` | Access token and refresh token | [Publish STAC](publish-stac.md) |
| Remove a STAC catalog | `POST /v1/edito/stac/jobs/removal` | Access token and refresh token | [Remove STAC](remove-stac.md) |
| Read publication job status and logs | `GET /v1/edito/stac/jobs/{job_id}` | Access token | [Publication jobs](publication-jobs.md) |
| List my publication jobs | `GET /v1/edito/stac/jobs` | Access token | [Publication jobs](publication-jobs.md) |
| Upload to S3 | `POST /v1/storage/s3/uploads` | Access token | [Upload to S3](upload-s3.md) |
| List my S3 buckets | `GET /v1/storage/s3/buckets` | Access token | [List S3 buckets](list-s3-buckets.md) |
| Transform NetCDF to Zarr | `POST /v1/conversions/netcdf/zarr` | Access token | [Transform NetCDF to Zarr](nc-to-zarr.md) |
| Convert GDB to GeoParquet | `POST /v1/conversions/gdb/parquet` | Access token | [Convert GDB to GeoParquet](gdb-to-parquet.md) |
| Convert Shapefile to GeoParquet | `POST /v1/conversions/shp/parquet` | Access token | [Convert Shapefile to GeoParquet](shp-to-parquet.md) |
| ISO19139 to STAC Item | `POST /v1/conversions/iso/stac-item` | Access token | [ISO19139 to STAC Item](iso-to-stac-item.md) |
| NetCDF metadata to STAC Item | `POST /v1/conversions/netcdf/stac-item` | Access token | [NetCDF to STAC Item](netcdf-to-stac-item.md) |
| Zarr metadata to STAC Item | `POST /v1/conversions/zarr/stac-item` | Access token | [Zarr to STAC Item](zarr-to-stac-item.md) |
| ARCO-ify a STAC Item's assets | `POST /v1/conversions/stac-item/arco` | Access token | [ARCO STAC Item](arco-stac-item.md) |
| Monitoring logs and status | `GET /v1/monitoring/logs`, `GET /v1/monitoring/status` | Access token | [Monitoring](monitoring.md) |

Protected features require an access token from the [token exchange](../auth.md); publishing and removing also send the refresh token.
