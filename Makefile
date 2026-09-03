# EDITO Ingestion Makefile

.PHONY: help install test docs-build docs-serve api-start api-stop api-restart docker-deploy

# Default target
help:
	@echo "EDITO Ingestion - Available commands:"
	@echo ""
	@echo "  install      - Install project deps (uv + npm)"

	@echo "  test          - Run tests"
	@echo "  docs-build    - Build the documentation with strict validation"
	@echo "  docs-serve    - Preview the documentation locally"
	@echo "  api-start     - Start the API server"
	@echo "  api-stop     - Stop the API server"
	@echo "  api-restart  - Restart the API server"
	@echo "  docker-deploy - Build and start services via docker compose"
	@echo ""

# Installation
install:
	sudo apt-get update
	curl -LsSf https://astral.sh/uv/install.sh | sh
	sudo apt-get install -y npm		## we need npm for the frontend
	sudo apt-get install -y mc		## we need mc for the storage
	uv sync

# Docker deploy (simple)
docker-deploy:
	docker compose -f deploy/docker/docker-compose.yml up -d --build

test:
	uv run pytest

docs-build:
	uv run --extra docs mkdocs build --strict

docs-serve:
	uv run --extra docs mkdocs serve --dev-addr 127.0.0.1:8001

# API Server Management
api-start:
	@echo "🚀 Starting API server on port 8000 with auto-reload..."
	@uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload

api-stop:
	@echo "🛑 Stopping API server..."
	@pkill -9 -f "python.*uvicorn\|python.*fastapi\|uvicorn.*main\|fastapi.*main\|ept start-api" || true
	@kill -9 $$(lsof -ti:8000) 2>/dev/null || true
	@echo "✅ API server stopped"

api-restart: api-stop
	@echo "⏳ Waiting for server to stop..."
	@sleep 2
	@$(MAKE) api-start
