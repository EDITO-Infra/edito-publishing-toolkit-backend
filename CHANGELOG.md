# Changelog

All notable changes to the EDITO Publishing Toolkit are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [1.1.0-beta] - 2026-10-19

### Added

- New features:
  - `arco-stac-item` — ARCO-ify STAC Item data assets (auto nc/gdb detection, explicit `conversions.toml` map, per-stage timing, job-runner operation)
  - `assemble-stac` — assemble loose STAC JSON files into one linked tree
  - `gdb-to-parquet`, `shp-to-parquet` — file geodatabase / shapefile to GeoParquet, incl. HTTP(S) `.zip` sources
  - `iso-to-stac-item` — ISO19139 XML to STAC Item conversion
  - `netcdf-to-stac`, `zarr-to-stac` — NetCDF / Zarr metadata to STAC Item
  - `stac-find`, `stac-get` — search and fetch STAC objects from the EDITO STAC API
  - `stac-build-item`, `stac-build-collection`, `stac-build-catalog` — STAC object builders
  - `stac-suggest-collection`, `stac-suggest-catalog` — collection/catalog suggestion with SPDX license matching
  - `list-s3-buckets` — list the caller's S3 buckets
  - `job-runner` — background runs for feature operations
- Monitoring: SQLite persistence of logs, run listing endpoints, DEBUG records retained in the buffer
- Single `VERSION` file as the source of truth for the version
- Auth: 10s leeway when validating bearer token expiry
- Hands-on tutorial: seven focused walkthroughs, end-to-end guide, ProjectsForum slide deck
- `ruff` as a dev dependency

### Changed

- `arco-stac-item` default output: a link or local Item with no
  `item_target`/`assets_target` is written to `s3://<bucket>/<item_id>.json`
  (bucket root, `oidc-<username>` by default) instead of failing
- S3-backed features now return browser-addressable `https://` asset hrefs
- Unified local output handling (`LocalOutput` field type, target file-name semantics, S3 env fallbacks)
- OpenAPI endpoints grouped into facet tags; versioned handbook served via mkdocs
- Auth logs downgraded from info to debug
- Tutorial rework: standalone scripts with inline API calls, workspace-unique item IDs

### Changed

- Item builders namespace derived ids with the caller's username (same scheme as the suggesters): when no `item_id` is given, `netcdf-to-stac` and `zarr-to-stac` prefer the dataset's own `id`, `identifier`, or `title` attribute (falling back to the source file/store name), and `iso-to-stac-item` prefixes the ISO `fileIdentifier` (title as last resort) — proposed ids become `<username>-<identifier>`

### Fixed

- Safe export names everywhere: any id used for an export file/folder name is the id itself when safe (alphanumerics plus `. _ -`), else slugified with a warning — `stac-suggest-catalog` copies collections under `<target>/<collection-id>/` (was `<target>/collections/<id>/`), `stac-suggest-collection` and `stac-assemble` slug unsafe item/child ids in the pystac tree, and the single-file exporters (`netcdf-to-stac`, `zarr-to-stac`, `iso-to-stac-item`, `arco-stac-item`, `nc-to-zarr`) slug id/file-name-derived outputs; caller-supplied file-name overrides go through the same check, and id-slug collisions are rejected
- `stac-suggest-catalog` returns browser-addressable `https://` URIs for the exported catalog/collection/item files (was `s3://`)
- `nc-to-zarr` for THREDDS DAP sources and S3 clients with ambient AWS profiles
- `arco-stac-item` S3 ops with stsonly STS credentials (missing `session_token` forwarding)
- `validate_stac` false negatives: interval-only items and WGS84 bounds
- Stale OpenAPI/index test expectations and ISO license test

## [1.0.1] - 2026-10-14

### Added

- Monitoring feature: in-memory log buffer and status endpoints

### Changed

- Auth logs downgraded from info to debug; buffer noise reduced

## [1.0.0] - 2026-10-09

### Added

- Core API shell: auth (`/v1/auth`), S3 upload (`/v1/storage/s3/upload`), STAC publish/remove with async job polling, `mycatalogs`
- Registry-driven feature system with `feature.toml` manifests
- RFC 7807 problem-detail errors with stable `reason` slugs
- Credential-safe logging (token diagnostics, payload sanitization)

### Security

- Dependency security update in `uv.lock`