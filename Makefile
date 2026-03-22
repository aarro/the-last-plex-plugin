PROVIDER_DIR := provider
UI_DIR       := provider/ui

.PHONY: test lint build dev docker-up docker-down docker-build logs

# Run all Python tests
test:
	uv --directory $(PROVIDER_DIR) run pytest

# Lint + auto-fix Python (ruff) and UI (biome)
lint:
	uv --directory $(PROVIDER_DIR) run ruff check --fix .
	uv --directory $(PROVIDER_DIR) run ruff format .
	bun run --cwd=$(UI_DIR) lint

# Build the React UI
build:
	bun run --cwd=$(UI_DIR) build

# Run backend dev server (with auto-reload)
dev:
	uv --directory $(PROVIDER_DIR) run uvicorn app:app --reload --port 8765

# Run UI dev server (proxies /api → localhost:8765)
dev-ui:
	bun run --cwd=$(UI_DIR) dev

# Docker
docker-build:
	docker compose --project-directory $(PROVIDER_DIR) build

docker-up:
	docker compose --project-directory $(PROVIDER_DIR) up -d

docker-down:
	docker compose --project-directory $(PROVIDER_DIR) down

logs:
	docker compose --project-directory $(PROVIDER_DIR) logs -f
