# Super AI Stack - developer tasks.
#
# These are conveniences only: every target shells out to a tool you can also run
# by hand. `make up` and `make down` work on Windows, macOS and Linux.

PYTHON ?= python
DEV    := $(PYTHON) scripts/dev.py
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: help setup up down status logs test lint fmt check clean docker-up docker-down models

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the virtualenv and install dependencies
	$(PYTHON) -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

up:  ## Start every service locally and wait for health
	$(DEV) up

down:  ## Stop every service this repo started
	$(DEV) stop

status:  ## Health-check every service port
	$(DEV) status

logs:  ## Tail the gateway log
	$(DEV) up gateway

test:  ## Run the test suite
	$(PYTHON) -m pytest

lint:  ## Check formatting and lint rules
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:  ## Apply formatting and safe autofixes
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

check:  ## Import-check every service module
	@for svc in gateway router memory agent model_manager speech image_gen vision \
	            llm_general llm_coding llm_reasoning; do \
		$(PYTHON) -c "import $$svc.main" && echo "  ok   $$svc" || echo "  FAIL $$svc"; \
	done

clean:  ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

docker-up:  ## Start the full stack with Compose
	$(COMPOSE) up -d --build

docker-down:  ## Stop the Compose stack
	$(COMPOSE) down

models:  ## Pull the default local models
	ollama pull phi3:mini
	ollama pull llama3.1
	ollama pull qwen2.5-coder:7b
	ollama pull deepseek-r1:8b
	ollama pull qwen2.5vl:3b
