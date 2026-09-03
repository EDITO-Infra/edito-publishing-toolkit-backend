# Docker usage

EPT can be run either from the published container image or built locally from the repository using Docker Compose.

## Get Docker

Install Docker from [Docker's official website](https://www.docker.com/get-started).

## Run the published container

EPT container images are published to GitHub Container Registry.

### Image versions

Use the `latest` tag to run the most recently published version:

```bash
ghcr.io/edito-infra/edito-publishing-toolkit-backend:latest
```

To use a specific EPT release, specify its version tag instead:

```bash
ghcr.io/edito-infra/edito-publishing-toolkit-backend:1.0.0
```

Using `latest` is convenient when you want the newest published version. Pinning a specific version is recommended when you need a reproducible deployment that should not change when a new EPT version is released.

### Run the latest version

Pull the latest published image:

```bash
docker pull ghcr.io/edito-infra/edito-publishing-toolkit-backend:latest
```

Start the container and publish the API on port `8000`:

```bash
docker run -d \
  --name ept-api \
  -p 8000:8000 \
  ghcr.io/edito-infra/edito-publishing-toolkit-backend:latest
```

### Run a specific version

For example, to run EPT `1.0.0`:

```bash
docker pull ghcr.io/edito-infra/edito-publishing-toolkit-backend:1.0.0

docker run -d \
  --name ept-api \
  -p 8000:8000 \
  ghcr.io/edito-infra/edito-publishing-toolkit-backend:1.0.0
```

The API is then available at:

* API index: `http://localhost:8000/v1`
* Swagger UI: `http://localhost:8000/docs`
* OpenAPI schema: `http://localhost:8000/openapi.json`

View the container logs:

```bash
docker logs -f ept-api
```

Stop and remove the container:

```bash
docker stop ept-api
docker rm ept-api
```

## Run from the repository

When working from a local checkout of the repository, use the Docker Compose configuration under `deploy/docker/`.

From the repository root:

```bash
cd deploy/docker
docker compose up --build
```

To run EPT in the background:

```bash
docker compose up --build -d
```

The Compose configuration builds EPT from the local source and publishes the API on port `8000`.

The API is then available at:

* API index: `http://localhost:8000/v1`
* Swagger UI: `http://localhost:8000/docs`
* OpenAPI schema: `http://localhost:8000/openapi.json`

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

Read the file without opening a shell:

```bash
docker compose exec ept-api tail -n 200 /tmp/ept/logs/api.log
```

Follow the file:

```bash
docker compose exec ept-api tail -f /tmp/ept/logs/api.log
```

The file is removed with the container. Use `docker compose logs` or configure a Docker logging driver when logs must be retained outside the container.

## Open a shell in the container

Open a shell in the running EPT service:

```bash
docker compose exec ept-api sh
```

Once inside the container:

```bash
tail -n 200 /tmp/ept/logs/api.log
tail -f /tmp/ept/logs/api.log
```

`docker compose exec` expects the Compose **service name** (`ept-api`), not the generated container name shown by `docker ps`.

## Stop EPT

Stop and remove the Compose containers:

```bash
docker compose down
```
