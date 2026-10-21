# EPT Hands-on Tutorial

Hands-on walkthroughs of the EDITO Publishing Toolkit — from metadata
records and NetCDF files to a validated, published STAC catalog, run against
real EMODnet records. Every guide works in the frontend or with
`curl`/`python`, and checks its result in a viewer as you go.

## Session setup (once)

Everything below needs an authenticated session — run one of these at the
start (or after a break: S3 sessions last 24 h, access tokens less). Both
take your EDITO username/password from the environment variables
`EDITO_USERNAME`/`EDITO_PASSWORD` when set, and prompt for them otherwise.

**Get an EPT API** — pick one:

1. **EDITO service playground** (zero install):
   [edito-publishing-toolkit-service](https://datalab.dive.edito.eu/launcher/service-playground/edito-publishing-toolkit-service)
2. **Clone the backend** and run it locally:

    ```bash
    git clone https://github.com/EDITO-Infra/edito-publishing-toolkit-backend
    cd edito-publishing-toolkit-backend && make api-start   # API on :8000
    ```

    Optionally run the public frontend image against it:

    ```bash
    docker run -d --name ept-frontend -p 3000:8080 \
      -e EPT_API_UPSTREAM=http://host.docker.internal:8000 \
      --add-host=host.docker.internal:host-gateway \
      ghcr.io/edito-infra/edito-publishing-toolkit-frontend:${EPT_VERSION}
    ```

3. **Pull both images** — no clone needed. Pick the newest version tag on
   the GHCR package pages
   ([backend](https://github.com/EDITO-Infra/edito-publishing-toolkit-backend/pkgs/container/edito-publishing-toolkit-backend),
   [frontend](https://github.com/EDITO-Infra/edito-publishing-toolkit-frontend/pkgs/container/edito-publishing-toolkit-frontend))
   and export it — both images are released together, so use the same tag
   for both:

    ```bash
    export EPT_VERSION=1.1.0-beta   # example — replace with the latest applicable tag

    # Backend — no env needed, defaults are baked in
    docker run -d --name ept-api -p 8000:8000 \
      -v ept_workspace:/app/workspace -v ept_data:/app/data \
      ghcr.io/edito-infra/edito-publishing-toolkit-backend:${EPT_VERSION}

    # Frontend — proxies /api to the backend
    docker run -d --name ept-frontend -p 3000:8080 \
      -e EPT_API_UPSTREAM=http://ept-api:8000 --link ept-api \
      ghcr.io/edito-infra/edito-publishing-toolkit-frontend:${EPT_VERSION}
    ```

   Details: [Docker usage](../../docker-usage.md).

**With curl** (needs `jq`):

```bash
export EPT_URL=http://localhost:8000     # or your service URL
[ -n "$EDITO_USERNAME" ] || read -rp "EDITO username: " EDITO_USERNAME
[ -n "$EDITO_PASSWORD" ] || { read -rsp "EDITO password: " EDITO_PASSWORD; echo; }
export EDITO_USERNAME EDITO_PASSWORD

curl -s -X POST $EPT_URL/v1/auth \
  -d "username=$EDITO_USERNAME" -d "password=$EDITO_PASSWORD" > auth.json
export TOKEN=$(jq -r .access_token auth.json)
export REFRESH_TOKEN=$(jq -r .refresh_token auth.json)
export S3_KEY=$(jq -r .s3_credentials.access_key_id auth.json)
export S3_SECRET=$(jq -r .s3_credentials.secret_access_key auth.json)
export S3_SESSION=$(jq -r .s3_credentials.session_token auth.json)
export BUCKET=oidc-$(echo "$EDITO_USERNAME" | tr '[:upper:]' '[:lower:]')
[ -n "$PROJECTID" ] || { read -rp "Project short name (must be unique — ids derive from it): " PROJECTID; echo; }
export PROJECTID=$(echo "$PROJECTID" | tr '[:upper:]' '[:lower:]')
export WORKSPACE=$PROJECTID-output
```

`auth.json` holds live tokens — keep it private (`rm auth.json` when done).

Ids are prefixed with `PROJECTID` — your project's short name, which **must
be unique** (ids and the workspace folder derive from it, so two projects
must not share one). Files live under `WORKSPACE` = `<projectid>-output`.
Note EPT's own auto-proposed ids are username-namespaced; in a shared
project session, pass explicit `<projectid>-…` ids like the walkthroughs do.

**With python** — every walkthrough snippet assumes `post`,
`EPT`, `TOKEN`, `REFRESH_TOKEN`, `S3`, `BUCKET`, `PROJECTID`, `WORKSPACE` and
`MINIO` are defined:

```python
import os, getpass, requests

EPT = os.environ.get("EPT_URL", "http://localhost:8000")   # or your service URL
user = os.environ.get("EDITO_USERNAME") or input("EDITO username: ")
password = os.environ.get("EDITO_PASSWORD") or getpass.getpass("EDITO password: ")
auth = requests.post(f"{EPT}/v1/auth",
                     data={"username": user, "password": password}).json()
TOKEN, REFRESH_TOKEN = auth["access_token"], auth["refresh_token"]
S3 = {"endpoint_url": "https://minio.dive.edito.eu",
      "access_key_id": auth["s3_credentials"]["access_key_id"],
      "secret_access_key": auth["s3_credentials"]["secret_access_key"],
      "session_token": auth["s3_credentials"]["session_token"]}
BUCKET = f"oidc-{user.lower()}"             # in a session or project, use its bucket
PROJECTID = (os.environ.get("EPT_PROJECTID") or input("Project short name (must be unique): ")).lower()
WORKSPACE = f"{PROJECTID}-output"
MINIO = "https://minio.dive.edito.eu"

def post(path, **body):
    """POST {EPT}{path} with the bearer token; the keywords are the JSON body."""
    r = requests.post(f"{EPT}{path}", json=body,
                      headers={"Authorization": f"Bearer {TOKEN}"})
    r.raise_for_status()
    return r.json()
```

`BUCKET` defaults to your personal `oidc-<username>` bucket; in a session or
project, pass its bucket instead. Everything lands under `<WORKSPACE>` so
parallel sessions never collide.

## Walkthroughs

Seven short guides, one capability each — every output feeds the next step:

1. [Transform data: NetCDF → Zarr, GDB zip → GeoParquet](walkthroughs/walkthrough-1-transform.md)
2. [A data item: the NetCDF → a STAC Item, then make it yours](walkthroughs/walkthrough-2-data-items.md)
3. [Items from metadata: ISO19139 records → STAC Items](walkthroughs/walkthrough-3-metadata-items.md)
4. [ARCO items: convert Items' data assets in place](walkthroughs/walkthrough-4-arco.md)
5. [Collections: suggest, export, make it yours](walkthroughs/walkthrough-5-collections.md)
6. [Catalog: assemble the final tree, validate](walkthroughs/walkthrough-6-catalog.md)
7. [Publish: into the EDITO STAC API](walkthroughs/walkthrough-7-publish.md)

> After publishing: your catalog is live in the EDITO STAC API, but **not
> necessarily public** — making it visible beyond your project requires a
> request to EDITO support.

Prefer the request level first? Read
[End-to-end publishing](../end-to-end-publishing.md).

## Slide deck

A Marp deck covering the same material lives next to these pages
(`ept-handson-tutorial.marp.md` + `edito2.css`). It is not served by the
handbook — render it yourself when you need to present:

```bash
cd docs/tutorials/ept-handson-tutorial
npx -y @marp-team/marp-cli@latest ept-handson-tutorial.marp.md \
  --theme edito2.css --html -o ept-handson-tutorial.html
```

Deck source is repo-only (`ept-handson-tutorial.marp.md`); render locally for PDF/PPTX options.
