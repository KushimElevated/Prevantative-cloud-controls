# Convenience wrappers. Every target is a plain docker compose / npm command you can run directly.
.PHONY: up down reset seed reconcile test test-frontend test-e2e logs lock openapi

up:            ## build and start db, migrations, backend, frontend (waits for health checks)
	docker compose up -d --build --wait

down:          ## stop containers (data volume is kept)
	docker compose down

reset:         ## drop schema, migrate, deterministic seed (repeatable)
	docker compose run --rm ops python -m app.cli reset

seed:          ## seed an empty database (refuses if data exists)
	docker compose run --rm ops python -m app.cli seed

reconcile:     ## idempotent expiry/drift reconciliation (scheduling seam)
	docker compose run --rm ops python -m app.cli reconcile

test:          ## backend pytest against the real PostgreSQL schema (separate ccp_test database)
	docker compose run --rm ops pytest

test-frontend: ## frontend type check and component tests (requires: cd frontend && npm ci)
	cd frontend && npx tsc --noEmit && npx vitest run

test-e2e:      ## browser smoke test of the main journey (requires a fresh `make reset` and a running stack)
	cd frontend && npx playwright test

logs:
	docker compose logs -f backend frontend

lock:          ## regenerate hash-pinned requirement exports from uv.lock
	cd backend && uv lock && uv export --frozen --no-dev --format requirements-txt --no-emit-project -o requirements.lock \
	  && uv export --frozen --only-group dev --format requirements-txt --no-emit-project -o requirements-dev.lock

openapi:       ## refresh docs/openapi.json from the running backend
	curl -s localhost:8000/api/openapi.json | python3 -m json.tool --sort-keys > docs/openapi.json
