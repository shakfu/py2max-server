
# Makefile for py2max-server project

.PHONY: help all build test test-verbose coverage lint format typecheck \
		qa check install dev clean reset ci check-wheel publish-test publish \
		serve repl demo

# Ports: HTTP on $(PORT), WebSocket on $(PORT)+1, REPL on $(PORT)+2.
PORT ?= 8000
# Patch served by `make serve`; override with PATCH=<file.maxpat>.
PATCH ?= examples/example.maxpat
# `make demo` serves a scratch copy instead, for experimenting without touching
# the tracked example above (relevant if you pass --auto-save, or just save).
DEMO_PATCH ?= outputs/example.maxpat

help: ## Show this help message
	@echo "Available commands:"
	@awk 'BEGIN {FS = ":.*##"; printf "\033[36m%-13s\033[0m %s\n", "Command", "Description"} /^[a-zA-Z_-]+:.*?##/ { printf "\033[36m%-13s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

all: qa ## Run all checks

build: ## Build wheel
	@uv build

test: ## Run tests
	@uv run pytest

test-verbose: ## Run tests with verbose output
	@uv run pytest -v

coverage: ## Generate HTML coverage report
	@mkdir -p outputs
	@uv run pytest --cov-report html:outputs/_covhtml --cov=py2max_server tests

lint: ## Run code linting (with autofix)
	@uv run ruff check . --fix

format: ## Run code formatting
	@uv run ruff format .

typecheck: ## Run type checking
	@uv run mypy py2max_server

qa: lint format typecheck test ## Lint, format, typecheck, and test

check: ## Run CI-style checks without modifying files
	@uv run ruff check .
	@uv run ruff format --check .
	@uv run mypy py2max_server

serve: ## Serve a patch in the browser editor (default PATCH=examples/example.maxpat)
	@test -f "$(PATCH)" || { \
		echo "no such patch: $(PATCH)"; \
		echo "usage: make serve [PATCH=<file.maxpat>] [PORT=8000]"; \
		exit 2; }
	@echo "Serving $(PATCH) (auto-save off -- press Save in the editor to write)"
	@uv run py2max-server serve "$(PATCH)" --port $(PORT)

repl: ## Connect a REPL client to a running server (make repl TOKEN=... [PORT=8000])
	@test -n "$(TOKEN)" || { \
		echo "usage: make repl TOKEN=<session-token> [PORT=8000]"; \
		echo "the server prints the token on startup (or set PY2MAX_REPL_TOKEN)"; \
		exit 2; }
	@uv run py2max-server repl localhost:$$(($(PORT) + 2)) --token "$(TOKEN)"

demo: ## Serve a throwaway copy of the example, leaving the tracked file alone
	@mkdir -p $(dir $(DEMO_PATCH))
	@cp $(PATCH) $(DEMO_PATCH)
	@echo "Copied $(PATCH) -> $(DEMO_PATCH)"
	@$(MAKE) serve PATCH=$(DEMO_PATCH) PORT=$(PORT)

install: ## Install package in development mode
	@uv sync

dev: install ## Set up development environment
	@echo "Development environment ready!"
	@echo "Activate with: source .venv/bin/activate"

clean: ## Clean build artifacts
	@rm -rf build/
	@rm -rf dist/
	@rm -rf *.egg-info/
	@rm -rf .pytest_cache/
	@rm -rf .mypy_cache/
	@rm -rf .ruff_cache/
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} +
	@rm -rf outputs/

reset: clean ## Reset development environment
	@rm -rf .venv

ci: ## Run CI-like checks locally (mirrors .github/workflows/ci.yml)
	@uv run pytest --cov=py2max_server
	@uv run ruff check .
	@uv run ruff format --check .
	@uv run mypy py2max_server

check-wheel: build ## Check wheel with twine
	@uvx twine check dist/*

publish-test: check-wheel ## Publish to PyPI Test
	@uvx twine upload --repository testpypi dist/*

publish: check-wheel ## Publish to PyPI
	@uvx twine upload dist/*
