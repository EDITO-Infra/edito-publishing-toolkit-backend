# Walkthrough 3 — Items from metadata

From ISO19139 records to STAC Items — two real EMODnet GeoNetwork records,
one call each:

- **Mangrove cover and composition (Essential Ocean Variable) in European
  jurisdictional waters (2025)** — EMODnet Seabed Habitats (record
  `0eeaeb31-c68b-4f1a-93d5-21f8134e5008`), data download is a GDB `.zip`
- **Heritage shipwrecks** — EMODnet Human Activities (record
  `e965088b-a265-4517-84df-c49b156af8a7`), same: a GDB `.zip`

Your ISO records already hold everything STAC needs — bbox, temporal extent,
contacts, constraints; EPT does the mapping. Each Item's assets carry the
record's online resources (the data downloads, WMS/WFS endpoints); a later
step converts those data assets to ARCO formats in place.

Works with the EPT frontend or `curl`/`python` — every step shows both.

**In the frontend:** *Metadata Conversion* → **ISO19139 to STAC Item** —
run it twice, once per record:

- **Mangrove:** `source_uri` =
  `https://emodnet.ec.europa.eu/geonetwork/srv/api/records/0eeaeb31-c68b-4f1a-93d5-21f8134e5008/formatters/xml`,
  `item_id` = `<projectid>-mangrove`, `bucket` = your bucket, `target` =
  `<workspace>/items/mangrove`
- **Shipwrecks:** `source_uri` =
  `https://emodnet.ec.europa.eu/geonetwork/srv/api/records/e965088b-a265-4517-84df-c49b156af8a7/formatters/xml`,
  `item_id` = `<projectid>-heritage-shipwrecks`, same `bucket`, `target` =
  `<workspace>/items/heritage-shipwrecks`

Both times: the S3 fields from your login, tick `overwrite`.

```bash
curl -X POST $EPT_URL/v1/metadata/iso-to-stac-item \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://emodnet.ec.europa.eu/geonetwork/srv/api/records/0eeaeb31-c68b-4f1a-93d5-21f8134e5008/formatters/xml",
    "item_id": "'$PROJECTID'-mangrove",
    "bucket": "'$BUCKET'", "target": "'$WORKSPACE'/items/mangrove",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "'$S3_KEY'", "secret_access_key": "'$S3_SECRET'",
    "session_token": "'$S3_SESSION'",
    "overwrite": true
  }' | jq .target_uri
```
Repeat for the shipwrecks record (`e965088b-a265-4517-84df-c49b156af8a7`,
`item_id` `$WORKSPACE-heritage-shipwrecks`, `target`
`$WORKSPACE/items/heritage-shipwrecks`).


Or do both records at once
```python
RECORDS = {"mangrove": "0eeaeb31-c68b-4f1a-93d5-21f8134e5008",
           "heritage-shipwrecks": "e965088b-a265-4517-84df-c49b156af8a7"}

for name, uuid in RECORDS.items():
    post("/v1/metadata/iso-to-stac-item",
         source_uri=f"https://emodnet.ec.europa.eu/geonetwork/srv/api/records/{uuid}/formatters/xml",
         item_id=f"{PROJECTID}-{name}",
         bucket=BUCKET, target=f"{WORKSPACE}/items/{name}",
         overwrite=True, **S3)
```

Notes:

- `item_id` is optional: when you leave it out, EPT proposes one from the
  ISO `fileIdentifier`, namespaced with your username. This walkthrough sets
  it explicitly with the project prefix (`<projectid>-…` — your project
  short name, which must be unique) so the file names stay predictable and
  project-scoped across the session.
- `collection_id` is optional — the Collection step stamps it, with the
  links, on the item copies; the catalog step nests Items inside their
  Collection (EDITO rejects Items that sit directly under a Catalog).
- The record needs a geographic bbox and usable temporal information.

**Next:** [Walkthrough 4 — ARCO items](walkthrough-4-arco.md)
