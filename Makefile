# Astroeasy Makefile
# Common development commands

.PHONY: help install install-dev test coverage lint format build build-docker clean fetch-test-data upload-test-data check-clean-tree check-version-unpublished check-tag-free version tag publish

# Use uv run to ensure we're using the virtual environment
PYTHON := uv run python

# Default target
help:
	@echo "Available targets:"
	@echo "  install       - Install package in current environment"
	@echo "  install-dev   - Install package with dev dependencies"
	@echo "  test          - Run tests"
	@echo "  coverage      - Run tests with coverage report"
	@echo "  lint          - Run linter (ruff)"
	@echo "  format        - Format code (ruff)"
	@echo "  build         - Build package (wheel and sdist)"
	@echo "  build-docker  - Build astrometry-cli Docker image"
	@echo "  clean         - Remove build artifacts"
	@echo ""
	@echo "Release (maintainers):"
	@echo "  publish       - clean + test + build + upload to PyPI (requires clean git tree)"
	@echo "  tag           - git tag v<version> from pyproject.toml and push it"
	@echo ""
	@echo "Test data:"
	@echo "  fetch-test-data      - Download test data from GitHub release"
	@echo "  upload-test-data     - Upload test data to GitHub release (maintainers)"
	@echo ""
	@echo "Index management:"
	@echo "  indices-download SERIES=5200_LITE OUTPUT=/path       - Download indices"
	@echo "  indices-examine  SERIES=5200_LITE INDEX_PATH=/path   - Examine indices"
	@echo ""
	@echo "Testing:"
	@echo "  test-install-local   - Test local astrometry.net installation"
	@echo "  test-install-docker  - Test Docker astrometry.net installation"

# Installation
install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[dev]"

# Test data management
fetch-test-data:
	@$(PYTHON) scripts/fetch_test_data.py

upload-test-data:
	@$(PYTHON) scripts/upload_test_data.py

# Testing
test: fetch-test-data
	uv run pytest tests/ -v

coverage: fetch-test-data
	uv run pytest tests/ -v \
    	--junitxml=reports/junit/junit.xml \
		--cov=astroeasy \
		--cov-report=term-missing \
		--cov-report=xml \
		--cov-report=html
	uv run genbadge tests -o tests.svg
	uv run genbadge coverage -i coverage.xml -o coverage.svg
	@echo "Coverage report: htmlcov/index.html"

badges: coverage
	@echo "Badges generated:"
	@ls -1 *.svg

# Code quality
lint:
	uv run ruff check astroeasy/

format:
	uv run ruff format astroeasy/
	uv run ruff check --fix astroeasy/

# Build
build:
	$(PYTHON) -m build

build-docker:
	docker build -t astrometry-cli astroeasy/dotnet/

# Clean
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf reports/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# Release
VERSION = $(shell $(PYTHON) -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

check-clean-tree:
	@test -z "$$(git status --porcelain)" || { echo "ERROR: git tree is dirty; commit or stash first"; git status --short; exit 1; }

check-version-unpublished:
	@if curl -sf https://pypi.org/pypi/astroeasy/$(VERSION)/json > /dev/null; then \
		echo "ERROR: astroeasy $(VERSION) is already on PyPI; bump version in pyproject.toml first"; exit 1; \
	fi
	@echo "PyPI check OK: $(VERSION) not yet published"

check-tag-free:
	@if git rev-parse -q --verify "refs/tags/v$(VERSION)" > /dev/null; then \
		echo "ERROR: local tag v$(VERSION) already exists"; exit 1; \
	fi
	@if git ls-remote --exit-code --tags origin "v$(VERSION)" > /dev/null 2>&1; then \
		echo "ERROR: tag v$(VERSION) already exists on origin"; exit 1; \
	fi
	@echo "Tag check OK: v$(VERSION) is free"

version:
	@echo $(VERSION)

# Tag the current commit with the pyproject.toml version and push the tag.
tag: check-clean-tree check-tag-free
	git tag v$(VERSION)
	git push origin v$(VERSION)

# Publish to PyPI. Guarded: refuses on a dirty tree or an already-published
# version, always rebuilds from scratch (stale dist/ can't be uploaded), and
# runs the test suite first. PyPI uploads are irreversible per version -
# bump pyproject.toml first.
publish: check-clean-tree check-version-unpublished clean test build
	uvx twine upload dist/*

# Index management
indices-download:
	@if [ -z "$(OUTPUT)" ]; then echo "Usage: make indices-download SERIES=5200_LITE OUTPUT=/path"; exit 1; fi
	$(PYTHON) -m astroeasy.cli indices download --series $(SERIES) --output $(OUTPUT)

indices-examine:
	@if [ -z "$(INDEX_PATH)" ]; then echo "Usage: make indices-examine SERIES=5200_LITE INDEX_PATH=/path"; exit 1; fi
	$(PYTHON) -m astroeasy.cli indices examine --series $(SERIES) --path $(INDEX_PATH)

# Installation verification
test-install-local:
	$(PYTHON) -m astroeasy.cli test-install --local

test-install-docker:
	$(PYTHON) -m astroeasy.cli test-install --docker astrometry-cli
