.PHONY: eval-tier1 hooks test-quality

PYTHON ?= python
REPO_ROOT := $(abspath ..)

eval-tier1:
	RUN_LIVE=0 W2_EVAL_NETWORK=disabled RERANKER=local COHERE_API_KEY= \
		$(PYTHON) -m evals.w2_runner run --tier recorded
	$(PYTHON) -m evals.artifact_scan evals/results-tier1.json evals/recordings

hooks:
	git -C "$(REPO_ROOT)" config core.hooksPath githooks
	@echo "Installed repository hooks from githooks/"

test-quality:
	$(PYTHON) -m pytest tests evals ops bruno corpus --tb=no --show-capture=no -q
