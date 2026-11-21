PYTHON ?= $(CURDIR)/.venv/bin/python

.PHONY: install test lint format-check type-check check docs build

install:
	$(PYTHON) -m pip install -e "./packages/provium[test,docs]"
	$(PYTHON) -m pip install -e "./packages/provium-pipeline" -e "./packages/provium-cloud[test]"
	$(PYTHON) -m pip install -e "./examples/provium-text-pipeline[test]"

test:
	cd packages/provium && $(PYTHON) -m pytest
	cd packages/provium-cloud && $(PYTHON) -m pytest --cov=provium_cloud --cov-branch --cov-fail-under=100
	cd examples/provium-text-pipeline && $(PYTHON) -m pytest

lint:
	cd packages/provium && $(PYTHON) -m ruff check src test typecheck
	cd packages/provium-cloud && $(PYTHON) -m ruff check src test infrastructure
	cd examples/provium-text-pipeline && $(PYTHON) -m ruff check src test

format-check:
	cd packages/provium && $(PYTHON) -m ruff format --check src test typecheck
	cd packages/provium-cloud && $(PYTHON) -m ruff format --check src test infrastructure
	cd examples/provium-text-pipeline && $(PYTHON) -m ruff format --check src test

type-check:
	cd packages/provium && $(PYTHON) -m pyright
	cd packages/provium-cloud && $(PYTHON) -m pyright
	cd packages/provium-cloud && $(PYTHON) -m pyright -p infrastructure

check: lint format-check type-check test

docs:
	$(PYTHON) -m mkdocs build --strict

build:
	$(PYTHON) -m build packages/provium --outdir dist/provium
	$(PYTHON) -m build packages/provium-cloud --outdir dist/provium-cloud

# Test-only AWS tooling; infrastructure is never provisioned by the runtime CLI.
CLOUD_ENV ?=
CLOUD_RESULTS ?=

.PHONY: cloud-live-install cloud-live-validate cloud-live-test cloud-live-cleanup
cloud-live-install:
	$(PYTHON) -m pip install -r packages/provium-cloud/infrastructure/aws/requirements.txt

cloud-live-validate:
	$(PYTHON) -c "from cfnlint.runner import main; main()" packages/provium-cloud/infrastructure/aws/stack.yaml

cloud-live-test: cloud-live-validate
	cd packages/provium-cloud && $(PYTHON) -m infrastructure.aws run $(if $(CLOUD_ENV),--env-file "$(abspath $(CLOUD_ENV))") $(if $(CLOUD_RESULTS),--results "$(CLOUD_RESULTS)")

cloud-live-cleanup:
	@test -n "$(CLOUD_RESULTS)" || (echo "Set CLOUD_RESULTS to the original run directory"; exit 2)
	cd packages/provium-cloud && $(PYTHON) -m infrastructure.aws cleanup $(if $(CLOUD_ENV),--env-file "$(abspath $(CLOUD_ENV))") --results "$(CLOUD_RESULTS)"
