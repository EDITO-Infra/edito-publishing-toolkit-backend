# Install

EPT is an API; the frontend is optional. Whichever way you run it, you end
up with the **UI** (browser), an **EPT_URL** for your script — or both.
Everything in the tutorials runs *in the service*; your machine only sends
HTTP requests.

## 1. Run it on EDITO — zero install

Launch the
[service playground](https://datalab.dive.edito.eu/launcher/service-playground/edito-publishing-toolkit-service)
from your personal space.
🎬 [Video — launching the service](videos/launch-ept-edito-service.mp4)

- **UI:** open your service URL, sign in with your EDITO account.
- **Script:** `export EPT_URL=<your service url>` and use the session
  block below.

## 2. Docker images — no clone

Pin a version — set `EPT_VERSION` to a tag from the GHCR package pages
([backend](https://github.com/EDITO-Infra/edito-publishing-toolkit-backend/pkgs/container/edito-publishing-toolkit-backend),
[frontend](https://github.com/EDITO-Infra/edito-publishing-toolkit-frontend/pkgs/container/edito-publishing-toolkit-frontend));
both images are released together, so use the same tag for both:

🎬 [Video — pulling the images](videos/ept-docker-pull-latest.mp4)

```bash
export EPT_VERSION=latest # or another tag

docker network create ept-net

docker run -d --name ept-api --network ept-net -p 8000:8000 \
  -v ept_workspace:/app/workspace -v ept_data:/app/data \
  ghcr.io/edito-infra/edito-publishing-toolkit-backend:${EPT_VERSION}

# frontend — proxies /api to the backend
docker run -d --name ept-frontend --network ept-net -p 3000:8080 \
  -e EPT_API_UPSTREAM=http://ept-api:8000 \
  ghcr.io/edito-infra/edito-publishing-toolkit-frontend:${EPT_VERSION}
```

bash shown — PowerShell: `$env:EPT_VERSION = "<tag>"`, and use
`$env:EPT_VERSION` in the run commands.

- **UI:** <http://localhost:3000>
- **Script:** `export EPT_URL=http://localhost:8000`

Details: [Docker usage](../docker-usage.md).

## 3. Clone the backend

```bash
git clone https://github.com/EDITO-Infra/edito-publishing-toolkit-backend
cd edito-publishing-toolkit-backend && make install && make api-start   # API on :8000
```

Optionally run the public frontend image against it:

```bash
docker run -d --name ept-frontend -p 3000:8080 \
  -e EPT_API_UPSTREAM=http://host.docker.internal:8000 \
  --add-host=host.docker.internal:host-gateway \
  ghcr.io/edito-infra/edito-publishing-toolkit-frontend:${EPT_VERSION}
```

- **UI:** <http://localhost:3000> (frontend container)
- **Script:** `export EPT_URL=http://localhost:8000`

## Log in

**Optional, bash/zsh only:** to keep credentials out of your shell history
and skip the prompts, copy the template, fill it in, and export it:

```bash
cp env.example .env      # fill in EDITO_USERNAME, EDITO_PASSWORD, EPT_URL
set -a && source .env && set +a   # export everything in .env to the shell
```

`.env` holds live credentials — never commit it. No POSIX shell handy
(Windows PowerShell, Jupyter)? Skip this — the python block below just
asks; answer the prompts. With the variables set, it runs without them.

**In the UI:** sign in with your EDITO account — the four S3 fields fill
from your session.

**In a script** — paste once per session (`requests`; already available in
the datalab environments):

```python
import os, getpass, requests
from pprint import pprint

EPT = os.environ.get("EPT_URL") or input("EPT API url [Enter = http://localhost:8000]: ").strip() or "http://localhost:8000"
MINIO = "https://minio.dive.edito.eu"
user = os.environ.get("EDITO_USERNAME") or input("EDITO username: ")
password = os.environ.get("EDITO_PASSWORD") or getpass.getpass("EDITO password: ")
PROJECTID = (os.environ.get("EPT_PROJECTID") or input("Project short name (must be unique): ")).lower()
bucket_in = os.environ.get("EPT_BUCKET") or input(f"Bucket [Enter = oidc-{user.lower()}]: ").strip()
BUCKET = bucket_in or f"oidc-{user.lower()}"
WORKSPACE = f"{PROJECTID}-output"

# /v1/auth takes URL-encoded form data — requests data= (not json=) sends that
auth = requests.post(f"{EPT}/v1/auth",
                     data={"username": user, "password": password}).json()
TOKEN, REFRESH_TOKEN = auth["access_token"], auth["refresh_token"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
c = auth["s3_credentials"]
S3 = {"endpoint_url": MINIO, "access_key_id": c["access_key_id"],
      "secret_access_key": c["secret_access_key"], "session_token": c["session_token"]}

def post(path, **body):    # POST {EPT}{path} with the bearer token; keywords = JSON body
    r = requests.post(f"{EPT}{path}", json=body, headers=AUTH)
    r.raise_for_status()
    return r.json()

def get(url, headers=None):    # GET a JSON url — public (MinIO) urls need no headers
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()

print(f"✅ authenticated at {EPT}; bucket {BUCKET}, workspace {WORKSPACE}/")
```

**With curl** (needs `jq` and a POSIX shell — on Windows, Git Bash or WSL):

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
rm auth.json   # it holds live tokens
```

**The variables** — each comes from the environment if set, else from a
prompt: `EPT_URL` — where the API runs · `PROJECTID` — your
project's short name, **must be unique** (ids and folders derive from it)
· `WORKSPACE` = `<projectid>-output` — everything lands under it, so
parallel sessions never collide · `BUCKET` — resolved in order: the
`EPT_BUCKET` env var if set, otherwise you're asked (Enter accepts your
personal `oidc-<username>` bucket). In a **shared or project bucket**, set
`EPT_BUCKET=<bucket>` or type its name at the prompt. EPT's own
auto-proposed ids are username-namespaced — in a shared session pass
explicit `<projectid>-…` ids, as the walkthroughs do.

**Ready?** [Full walkthrough — python](full-walkthrough-python.md).
