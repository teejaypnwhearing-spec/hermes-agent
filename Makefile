# =============================================================================
# FSH Command Center — Makefile
# =============================================================================
# Prerequisites: docker, docker compose, python3, psql (or run via docker)
#
# Quick start:
#   make env       → create .env from .env.example
#   make up        → start postgres + n8n + fsh-server
#   make migrate   → apply database schema
#   make test      → run FSH test suite
#   make logs      → tail all service logs
#   make down      → stop all services
# =============================================================================

.PHONY: help env up down migrate test logs shell-db lint install check

# Default: show help
help:
	@echo ""
	@echo "FSH Command Center"
	@echo "=================="
	@echo "  make env        Create .env from .env.example"
	@echo "  make install    Install Python dependencies"
	@echo "  make up         Start all services (postgres + n8n + fsh-server)"
	@echo "  make migrate    Apply database schema to postgres"
	@echo "  make test       Run FSH test suite"
	@echo "  make lint       Run flake8 on FSH adapters"
	@echo "  make logs       Tail all service logs"
	@echo "  make shell-db   psql shell into fsh_command_center"
	@echo "  make down       Stop all services"
	@echo "  make check      Validate schema JSON files"
	@echo ""

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "✓ Created .env from .env.example"; \
		echo "  → Edit .env and fill in: ANTHROPIC_API_KEY, ABACUS_API_KEY, etc."; \
	else \
		echo "  .env already exists — skipping"; \
	fi

install:
	pip install -r requirements.fsh.txt
	pip install -e "."
	@echo "✓ Dependencies installed"

# ---------------------------------------------------------------------------
# Docker services
# ---------------------------------------------------------------------------
up: env
	docker compose -f docker-compose.fsh.yml up -d --build
	@echo ""
	@echo "✓ Services started:"
	@echo "  Postgres    → localhost:5432  (db: fsh_command_center)"
	@echo "  n8n         → http://localhost:5678"
	@echo "  FSH Server  → http://localhost:8000"
	@echo ""
	@echo "  Run 'make migrate' to apply the database schema."

down:
	docker compose -f docker-compose.fsh.yml down
	@echo "✓ All services stopped"

logs:
	docker compose -f docker-compose.fsh.yml logs -f

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
migrate:
	@echo "Applying FSH schema to postgres..."
	@if [ -z "$$DATABASE_URL" ]; then \
		export DATABASE_URL="postgresql://fsh_user:$${POSTGRES_PASSWORD:-fsh_dev_password}@localhost:5432/fsh_command_center"; \
	fi; \
	docker exec -i fsh_postgres psql -U fsh_user -d fsh_command_center \
		< fsh-command-center/database/core_schema.sql && \
	echo "✓ Schema applied successfully"

shell-db:
	docker exec -it fsh_postgres psql -U fsh_user -d fsh_command_center

migrate-check:
	@echo "Checking schema was applied..."
	@docker exec fsh_postgres psql -U fsh_user -d fsh_command_center \
		-c "\dt" | grep -E "tasks|gridline_leads|approval_requests|dead_letter_queue" \
		&& echo "✓ Schema tables present" || echo "✗ Schema not applied — run: make migrate"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test:
	@echo "Running FSH test suite..."
	cd fsh-command-center && python3 -m pytest tests/ -v --tb=short --override-ini="addopts="

test-unit:
	cd fsh-command-center && python3 -m pytest tests/ -v --tb=short --override-ini="addopts=" -k "not integration"

test-integration:
	@echo "Integration tests require real API keys - ensure .env is populated"
	cd fsh-command-center && python3 -m pytest tests/ -v --tb=short --override-ini="addopts=" -m integration

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint:
	python3 -m flake8 fsh-command-center/adapters/ fsh-command-center/config/ \
		fsh-command-center/orchestrator/ \
		--max-line-length=120 --ignore=E501,W503

check:
	@echo "Validating JSON schemas..."
	@python3 -c "import json; json.load(open('fsh-command-center/schema/task_schema_v1.0.0.json'))" \
		&& echo "✓ task_schema_v1.0.0.json valid"
	@python3 -c "import json; json.load(open('fsh-command-center/schema/task_schema_v1.0.1.json'))" \
		&& echo "✓ task_schema_v1.0.1.json valid"
	@python3 -c "
import sys; sys.path.insert(0,'fsh-command-center')
from config.pillar_defaults import PILLAR_DEFAULTS
for p, d in PILLAR_DEFAULTS.items():
    print(f'  ✓ pillar={p}: engine={d.execution_engine} approval={d.approval_level}')
"
	@echo "✓ All checks passed"

# ---------------------------------------------------------------------------
# Dev server (no Docker)
# ---------------------------------------------------------------------------
dev-server:
	@echo "Starting FSH webhook server locally (no Docker)..."
	cd fsh-command-center && \
	PYTHONPATH=.. uvicorn orchestrator.webhook_server:app \
		--host 0.0.0.0 --port 8000 --reload

# ---------------------------------------------------------------------------
# n8n workflow import
# ---------------------------------------------------------------------------
import-workflows:
	@echo "Importing n8n FSH workflows..."
	@for f in fsh-command-center/n8n-workflows/*.json; do \
		echo "  Importing $$f..."; \
		curl -sf -X POST http://localhost:5678/api/v1/workflows \
			-H "Content-Type: application/json" \
			-H "X-N8N-API-KEY: $${N8N_API_KEY}" \
			-d @$$f && echo " ✓" || echo " ✗ (check N8N_API_KEY in .env)"; \
	done
