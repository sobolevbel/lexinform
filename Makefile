.DEFAULT_GOAL := help

UV ?= uv
ARGS ?=
CORPUS ?=
CORPUS_OUTPUT ?=
CORPUS_PROFILES ?= extraction,selection,comparison,delivery
CORPUS_SUITE ?= manifest
CORPUS_MODE ?=
CORPUS_REFERENCE ?=
CORPUS_REFERENCE_SHA256 ?=
RUN = $(UV) run --frozen
WEB = $(RUN) --all-packages --all-groups
DOCS = $(RUN) --group docs
BOT_PATHS = src tests tools
WEB_PATHS = web/src web/tests web/stubs

.PHONY: help sync test test-unit test-scenario test-local-http test-integration coverage corpus-check lint typecheck format format-check check docs docs-build docs-check web-sync web-test web-lint web-typecheck web-format web-check web-serve

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  make %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\nExtra arguments: make test ARGS="tests/scenario/test_tracking.py -k closure -vv"\n'

sync: ## Install bot and development dependencies from uv.lock
	$(UV) sync --frozen

test: ## Run offline bot tests (ARGS="path -k expression -vv")
	$(RUN) pytest $(ARGS)

test-unit: ## Run bot unit tests
	$(RUN) pytest tests/unit $(ARGS)

test-scenario: ## Run bot pipeline scenarios
	$(RUN) pytest tests/scenario $(ARGS)

test-local-http: ## Run optional CLI tests with a local HTTP server
	$(RUN) pytest -m local_http $(ARGS)

test-integration: ## Run live integration tests (network required)
	$(RUN) pytest -m integration $(ARGS)

coverage: ## Run bot tests with a coverage report
	$(RUN) pytest --cov --cov-report=term-missing $(ARGS)

corpus-check: ## Replay explicit offline corpus/output paths (CORPUS=... CORPUS_OUTPUT=...)
	@test -n "$(CORPUS)" -a -n "$(CORPUS_OUTPUT)" || { echo "Set CORPUS and CORPUS_OUTPUT explicitly"; exit 2; }
	$(RUN) python -m tools.corpus_check --corpus "$(CORPUS)" --output "$(CORPUS_OUTPUT)" --profiles "$(CORPUS_PROFILES)" --suite "$(CORPUS_SUITE)" $(if $(CORPUS_MODE),--mode "$(CORPUS_MODE)") $(if $(CORPUS_REFERENCE),--reference "$(CORPUS_REFERENCE)") $(if $(CORPUS_REFERENCE_SHA256),--reference-sha256 "$(CORPUS_REFERENCE_SHA256)")

lint: ## Check bot and tools with Ruff
	$(RUN) ruff check $(BOT_PATHS)

typecheck: ## Run strict mypy for the bot and tests
	$(RUN) mypy

format: ## Format bot, tests and tools in place
	$(RUN) ruff format $(BOT_PATHS)

format-check: ## Check formatting without changing files
	$(RUN) ruff format --check $(BOT_PATHS)

check: ## Check tests, types, lint and formatting (no source edits)
	$(MAKE) test
	$(MAKE) typecheck
	$(MAKE) lint
	$(MAKE) format-check

docs: ## Serve documentation at http://127.0.0.1:8001
	$(DOCS) zensical serve $(ARGS)

docs-build: ## Build documentation in strict mode
	$(DOCS) zensical build --strict

docs-check: docs-build ## Build docs and validate local links, anchors and contents
	$(DOCS) python tools/check_docs.py

web-sync: ## Install all workspace packages and development tools (as in CI)
	$(UV) sync --frozen --all-packages --all-groups

web-test: ## Run web tests (local PostgreSQL required)
	$(WEB) pytest -c web/pyproject.toml web/tests $(ARGS)

web-lint: ## Check web lint and formatting without changing files
	$(WEB) ruff check $(WEB_PATHS)
	$(WEB) ruff format --check $(WEB_PATHS)

web-typecheck: ## Run strict mypy for the web package
	$(WEB) mypy --config-file web/pyproject.toml web/src web/tests

web-format: ## Format web code, tests and stubs in place
	$(WEB) ruff format $(WEB_PATHS)

web-check: ## Check web code, Django and migration drift (PostgreSQL required)
	$(MAKE) web-lint
	$(MAKE) web-typecheck
	$(WEB) python web/manage.py check
	$(WEB) python web/manage.py makemigrations --check --dry-run
	$(MAKE) web-test

web-serve: ## Start the Django development server (PostgreSQL required)
	$(WEB) python web/manage.py runserver $(ARGS)
