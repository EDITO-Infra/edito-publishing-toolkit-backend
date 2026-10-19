# EDITO Publishing Toolkit Makefile

.PHONY: help install test docs-build docs-slides docs-serve api-start api-stop api-restart docker-deploy

# Default target
help:
	@echo "EDITO Publishing Toolkit - Available commands:"
	@echo ""
	@echo "  install       - Install uv if missing and sync project dependencies"
	@echo "  test          - Run tests"
	@echo "  docs-build    - Build the documentation with strict validation"
	@echo "  docs-slides   - Render Marp slide decks (workshops) to HTML for the docs site"
	docs-serve    - Preview the documentation locally
	@echo "  api-start     - Start the API server"
	@echo "  api-stop      - Stop the API server (Unix: needs pkill/lsof)"
	@echo "  api-restart   - Restart the API server (Unix)"
	@echo "  docker-deploy - Build and start services via Docker Compose"
	@echo ""

# Installation: bootstraps uv if missing (Linux/macOS), then syncs dependencies.
install:
	command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync

# Docker deploy (simple)
docker-deploy:
	docker compose -f deploy/docker/docker-compose.yml up -d --build

test:
	uv run pytest

docs-build:
	uv run mkdocs build --strict

# Marp decks are committed as HTML next to their source so the mkdocs site
# picks them up; re-run this after editing any *.marp.md (needs Node 20+).
docs-slides:
	cd docs/tutorials/ept-handson-tutorial && \
	npx -y @marp-team/marp-cli@latest ept-handson-tutorial.marp.md \
		--theme edito2.css --html -o ept-handson-tutorial.html

docs-serve:
	uv run mkdocs serve --dev-addr 127.0.0.1:8001

# API Server Management
api-start:
	@echo "Starting API server on port 8000 with auto-reload..."
	@uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload

api-stop:
	@echo "Stopping API server..."
	@pkill -9 -f "uvicorn ept.api.main" || true
	@kill -9 $$(lsof -ti:8000) 2>/dev/null || true
	@echo "API server stopped"

api-restart: api-stop
	@echo "Waiting for server to stop..."
	@sleep 2
	@$(MAKE) api-start
