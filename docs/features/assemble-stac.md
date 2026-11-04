# STAC Assemble

Assemble loose STAC JSON files (catalogs, collections, items) from a local
folder, a `.zip` archive, or an `s3://` source into one valid, linked STAC
tree, and export it to S3 (by default your personal `oidc-<username>` bucket)
or a directory on the EPT service filesystem.

Requires `Authorization: Bearer <access_token>`.

## Endpoint

```
POST /v1/stac/assemble
```

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/assemble" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
        "source_uri": "s3://oidc-<username>/stac-draft.zip",
        "target": "stac-tree",
        "endpoint_url": "https://minio.dive.edito.eu",
        "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
        "secret_access_key": "<secret_access_key>",
        "session_token": "<session_token>",
        "overwrite": true
      }'
```

## Request

| Field | Type | Description |
|-------|------|-------------|
| `source_uri` | string | Local directory, local/remote `.zip` archive, or `s3://` URI of a `.zip` object or folder prefix. Every `*.json` file is read as a candidate STAC object. |
| `target` | string | S3 destination prefix relative to `bucket` (no `s3://` scheme) the tree is exported into. Required unless `local_output` is set. |
| `bucket` | string | Destination S3 bucket. Defaults to your personal EDITO bucket `oidc-<username>` derived from your bearer token; other buckets you can access, e.g. project buckets `project-<projectid>`, are equally valid. Ignored when `local_output` is set. |
| `local_output` | string | Directory on the EPT service filesystem to write the tree into instead of S3. Takes precedence over `target`/`bucket` and excludes S3 credentials. |
| `overwrite` | bool | Overwrite existing files or S3 objects. Default `true`. |
| `endpoint_url` / `access_key_id` / `secret_access_key` / `session_token` | string | Optional; only needed for S3 sources/targets. Use the request-scoped credentials from `POST /v1/auth`. |

## Structure resolution

The tree structure is resolved from, in order:

1. The files' own STAC links (`parent`/`child`/`item`/`collection`); for
   items, the `collection` property takes precedence over `collection`
   link hrefs.
2. The source folder layout as fallback.

Derivable fields (ids, `stac_version`, bboxes, extents) are filled in from the
items. Every exported object is schema-validated.

## Response

| Field | Description |
|-------|-------------|
| `source_uri`, `target_uri` | Echo of the source; `target_uri` is where the tree was exported (an `s3://` URI or a local path). |
| `root_path` | Path of the root object, relative to `target_uri`. |
| `objects` | One entry per written file: `path`, `object_type`, `object_id`, `validation_issues`. |
| `uploaded` | S3 objects written (empty when exporting to `local_output`). |
| `warnings` | Non-fatal issues encountered during assembly. |

## Notes

- Export names are safe: folder/file names derived from ids are slugified when the id is unsafe as a file/folder name (with a warning); ids themselves are unchanged, and two ids slugging to the same name in one folder are rejected.
- Companion to the STAC builders: build catalogs, collections, and items with
  plain-href links, then assemble them into one linked, validated tree.
- Use [Upload to S3](upload-s3.md) or [Transform NetCDF to Zarr](nc-to-zarr.md)
  first if the assets behind the items are not yet in S3.
