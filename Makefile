.PHONY: up up-gpu down down-gpu scale logs logs-ingest logs-process logs-dashboard psql capture build pull help

# CPU pipeline (GPU stays free)
up:
	docker compose up -d

# Add the LLM coach (uses GPU)
up-gpu:
	docker compose --profile gpu up -d

# Stop CPU pipeline
down:
	docker compose down

# Free the GPU — CPU pipeline keeps running
down-gpu:
	docker compose --profile gpu down

# Burn through a lap backlog with N workers (default 4)
scale N=4:
	docker compose up -d --scale process=$(N)

# Stream all logs
logs:
	docker compose logs -f

logs-ingest:
	docker compose logs -f ingest

logs-process:
	docker compose logs -f process

logs-dashboard:
	docker compose logs -f dashboard-v2

# Open a psql shell in the db container
psql:
	docker compose exec db psql -U coach

# Run the host capture agent (PowerShell)
capture:
	powershell -ExecutionPolicy Bypass -File capture/run_capture.ps1

# Rebuild images (after code changes)
build:
	docker compose build

# Pull latest base images
pull:
	docker compose pull db coach-llm

help:
	@echo "  make up            CPU pipeline (db + ingest + process + dashboard)"
	@echo "  make up-gpu        Add LLM coach (GPU)"
	@echo "  make down          Stop CPU pipeline"
	@echo "  make down-gpu      Free GPU, keep CPU pipeline running"
	@echo "  make scale N=4     Run N parallel process workers"
	@echo "  make logs          Stream all logs"
	@echo "  make logs-process  Stream process worker logs"
	@echo "  make psql          Open psql shell in db container"
	@echo "  make capture       Launch host capture agent (PowerShell)"
	@echo "  make build         Rebuild pipeline image"
