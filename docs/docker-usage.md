# Docker usage

EPT can be run with Docker Compose using the configuration under `deploy/docker/`.

## Get Docker

Install Docker from [Docker's official website](https://www.docker.com/get-started).

## Start EPT

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
