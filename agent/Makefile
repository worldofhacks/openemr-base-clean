.PHONY: eval-tier1 hooks record-evals test-quality verify-recordings

PYTHON ?= python
REPO_ROOT := $(abspath ..)

eval-tier1:
	$(PYTHON) -m evals.refresh_recordings --check
	RUN_LIVE=0 W2_EVAL_NETWORK=disabled RERANKER=local COHERE_API_KEY= \
		$(PYTHON) -m evals.w2_runner run --tier recorded
	$(PYTHON) -m evals.artifact_scan --eval-result evals/results-tier1.json evals/recordings

record-evals:
	RUN_LIVE=0 W2_EVAL_NETWORK=disabled ANTHROPIC_API_KEY= COHERE_API_KEY= \
		$(PYTHON) -m evals.refresh_recordings --write
	$(MAKE) PYTHON="$(PYTHON)" eval-tier1

verify-recordings:
	$(PYTHON) -m evals.refresh_recordings --check

hooks:
	git -C "$(REPO_ROOT)" config core.hooksPath githooks
	@echo "Installed repository hooks from githooks/"

test-quality:
	$(PYTHON) -m pytest tests evals ops bruno corpus --tb=no --show-capture=no -q
