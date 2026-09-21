.PHONY: help install test lint fmt golden clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install into the current environment
	uv pip install -e ".[dev]"
	@echo "installed; try: isoforge doctor"

test: ## Run the test suite
	uv run --extra dev pytest -q

lint: ## Check formatting and lint
	uv run --extra dev ruff check isoforge tests
	uv run --extra dev ruff format --check isoforge tests

fmt: ## Format sources
	uv run --extra dev ruff format isoforge tests

golden: ## Regenerate render golden files (review the diff before committing)
	ISOFORGE_UPDATE_GOLDEN=1 uv run --extra dev pytest -q
	@echo "regenerated; verify with: git diff testdata/golden"

build: ## Build a wheel
	uv build --wheel

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
