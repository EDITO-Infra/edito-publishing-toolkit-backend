# Docker usage

EPT can be run with Docker Compose using the configuration under `deploy/docker/`.

## Get Docker

Install Docker from [Docker's official website](https://www.docker.com/get-started).

## Start EPT from prebuilt images (no clone)

Both the API and the frontend are published on GHCR — pull and run them
without cloning any repository:

```bash
# Backend — no env needed, defaults are baked in
docker run -d --name ept-api -p 8000:8000 \
  -v ept_workspace:/app/workspace -v ept_data:/app/data \
  ghcr.io/edito-infra/edito-publishing-toolkit-backend:1.1.0-beta

# Frontend — proxies /api to the backend
docker run -d --name ept-frontend -p 3000:8080 \
  -e EPT_API_UPSTREAM=http://ept-api:8000 --link ept-api \
  ghcr.io/edito-infra/edito-publishing-toolkit-frontend:1.1.0-beta
```

- Frontend (UI): `http://localhost:3000` — it proxies `/api` to the backend
- API: `http://localhost:8000` · Swagger UI: `http://localhost:8000/docs`

No environment variables are required — the image bakes in the default
service URLs (publishing service, STAC API, Keycloak, MinIO) and client ID.
Set `EDITO_*`/`PUBLISHING_API_URL` with `-e` only to override them.

The `workspace/` and `data/` mounts keep exports and the deployment's
`data/owner.state` across container removals — same behaviour as the
[from-source setup](#start-ept-from-source) below.

## Start EPT from source

From the repository root:

```bash
cd deploy/docker
docker compose up --build
```

To run in the background:

```bash
docker compose up --build -d
```

The API is then available at:

- API index: `http://localhost:8000/v1`
- Swagger UI: `http://localhost:8000/docs`
- OpenAPI schema: `http://localhost:8000/openapi.json`

## Check the container

Show the services managed by the Compose project:

```bash
docker compose ps
```

The Compose service is named `ept-api`. Use that service name with `docker compose` commands.

## View logs

Follow the API console logs:

```bash
docker compose logs -f ept-api
```

EPT also writes an ephemeral log file inside the container at:

```text
/tmp/ept/logs/api.log
```

Read or follow it inside the running container with, for example, `docker compose exec ept-api cat /tmp/ept/logs/api.log` or a `tail`/`Get-Content -Wait` variant after opening a shell.

The file is removed with the container. Use `docker compose logs` or configure a Docker logging driver when logs must be retained outside the container.

## Open a shell in the container

Open a shell in the running EPT service:

```bash
docker compose exec ept-api sh
```

Once inside the container, read `/tmp/ept/logs/api.log` with the tools available in the image (for example `cat` or `tail`).

`docker compose exec` expects the Compose **service name** (`ept-api`), not the generated container name shown by `docker ps`.

## Stop EPT

Stop and remove the Compose containers:

```bash
docker compose down
```

## Persisted state

`workspace/` and `data/` next to the compose file are bind-mounted into the container, so they survive rebuilds, `down`, and even `down -v` (which only removes named volumes). `data/owner.state` records which EDITO user claimed this deployment — to release it, delete that file (root-owned when the container runs as root):

```bash
docker compose run --rm --user 0:0 --no-deps ept-api rm /app/data/owner.state
```
