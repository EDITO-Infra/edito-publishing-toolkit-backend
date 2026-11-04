# Session setup

Get an EPT API running first — [Install](../install.md): the EDITO
service (zero install), docker, or a clone. Then either open the **UI**
in your browser and sign in, or point your scripts at it — set `EPT_URL`
in the env file below.

Everything in the walkthroughs runs *in the service*; your machine only
sends HTTP requests.

## Log in

One env file sets everything — credentials, `EPT_URL`, and optionally
bucket and project id — and keeps them out of your shell history:

```bash
cp env.example .env      # fill it in (below), then:
set -a && source .env && set +a   # export everything in .env to the shell
```

`env.example` — fill in the first three; leave the optional two empty to
be prompted:

```bash
EDITO_USERNAME=your-edito-username
EDITO_PASSWORD=your-edito-password
EPT_URL=<your service url> OR http://localhost:8000 OR your custom address

# optional — leave empty to be prompted (bucket: Enter accepts oidc-<username>)
EPT_BUCKET=
EPT_PROJECTID=
```

`.env` holds live credentials — never commit it. With it sourced, the
blocks below run without prompts; anything left empty is asked (bucket:
Enter accepts your personal `oidc-<username>` bucket). No POSIX shell
handy (Windows PowerShell, Jupyter)? Skip this — the python block below
just asks; answer the prompts.

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
export BUCKET=${EPT_BUCKET:-oidc-$(echo "$EDITO_USERNAME" | tr '[:upper:]' '[:lower:]')}
[ -n "$EPT_PROJECTID" ] || { read -rp "Project short name (must be unique — ids derive from it): " EPT_PROJECTID; echo; }
export PROJECTID=$(echo "$EPT_PROJECTID" | tr '[:upper:]' '[:lower:]')
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

**Ready?** Start at [Walkthrough 1 — Transform data](walkthroughs/walkthrough-1-transform.md).
