.DEFAULT_GOAL := help
PY := .venv/bin/python

.PHONY: help setup lint fmt test check clean

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-8s %s\n", $$1, $$2}'

setup: ## Create .venv and install dev dependencies (uv, falling back to pip)
	@scripts/setup.sh

lint: ## Ruff lint + format check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt: ## Apply ruff formatting and safe fixes
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

test: ## Run the pytest suite
	$(PY) -m pytest

check: lint test ## Lint and test - what CI runs

clean: ## Remove caches and build output
	rm -rf .pytest_cache .ruff_cache dist build *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
