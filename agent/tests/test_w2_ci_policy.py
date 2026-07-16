"""Static policy guards for the Week 2 gate and exact-SHA deployment workflows."""

from __future__ import annotations

import json
from pathlib import Path
import re


_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def test_eval_workflow_is_fork_safe_scanned_and_quality_gated() -> None:
    workflow = _read(".github/workflows/agent-eval-gate.yml")

    assert "pull_request_target" not in workflow
    assert "W2_EVAL_NETWORK: disabled" in workflow
    assert "COHERE_API_KEY: ''" in workflow
    assert "needs: [quality, eval-tier1]" in workflow
    assert workflow.count("SOURCE_SHA:") == 2
    assert workflow.count("github.event.pull_request.head.sha") >= 3
    assert workflow.count("python -m evals.artifact_scan") == 2
    assert workflow.count("if: success()") == 2
    assert workflow.index("python -m evals.artifact_scan evals/results-tier1.json") < (
        workflow.index("name: eval-results-tier1")
    )
    assert workflow.index("python -m evals.artifact_scan evals/results-tier2.json") < (
        workflow.index("name: eval-results-tier2-live")
    )
    regression = next(
        line for line in workflow.splitlines() if "pytest tests evals" in line
    )
    assert "--tb=no" in regression
    assert "--show-capture=no" in regression


def test_new_agent_workflows_pin_third_party_actions_to_commits() -> None:
    for relative in (
        ".github/workflows/agent-eval-gate.yml",
        ".github/workflows/agent-quality.yml",
        ".github/workflows/agent-deploy.yml",
    ):
        workflow = _read(relative)
        action_refs = re.findall(r"uses: actions/[^@\s]+@([^\s]+)", workflow)
        assert action_refs
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)


def test_deploy_installs_locked_cli_before_exposing_production_token() -> None:
    workflow = _read(".github/workflows/agent-deploy.yml")
    package = json.loads(_read(".github/railway-cli/package.json"))

    assert "npx" not in workflow
    assert "npm ci --ignore-scripts --prefix .github/railway-cli" in workflow
    assert workflow.index("npm ci --ignore-scripts") < workflow.index("RAILWAY_TOKEN")
    assert package["dependencies"]["@railway/cli"] == "5.26.1"
    assert package["overrides"]["tar"] == "7.5.20"
    assert (_ROOT / ".github/railway-cli/package-lock.json").is_file()


def test_deploy_uses_an_exact_sha_context_and_explicit_worker_config() -> None:
    workflow = _read(".github/workflows/agent-deploy.yml")

    archive = 'git archive "$EVALUATED_SHA" agent'
    worker_config = (
        'cp "$worker_context/railway.worker.json" '
        '"$worker_context/railway.json"'
    )
    worker_deploy = '"$railway" up --service document-worker --ci'

    assert workflow.count('"$railway" up --service agent --ci --detach') == 1
    assert workflow.count(worker_deploy) == 1
    assert archive in workflow
    assert '--strip-components=1' in workflow
    assert worker_config in workflow
    assert '"deploy"]["startCommand"]' in workflow
    assert workflow.index(archive) < workflow.index(worker_config) < workflow.index(
        worker_deploy
    )
    web_deploy = '"$railway" up --service agent --ci --detach'
    assert workflow.index(worker_deploy) < workflow.index(web_deploy)
    assert worker_deploy + " --detach" not in workflow
    assert workflow.count('set "DEPLOYMENT_SHA=$EVALUATED_SHA" --skip-deploys') == 2
    assert "python agent/scripts/verify_deployed_sha.py" in workflow


def test_gitlab_gate_scans_before_success_only_artifact_retention() -> None:
    pipeline = _read(".gitlab-ci.yml")

    assert "python -m evals.w2_runner run --tier recorded" in pipeline
    assert "python -m evals.artifact_scan evals/results-tier1.json" in pipeline
    assert "when: on_success" in pipeline
    assert "when: always" not in pipeline
    bridge = pipeline.split("github-exact-sha-bridge:", maxsplit=1)[1]
    assert "before_script: []" in bridge
    assert bridge.index("before_script: []") < bridge.index("verify_github_gate.py")


def test_quality_recorded_gate_binds_pr_head_sha_explicitly() -> None:
    workflow = _read(".github/workflows/agent-quality.yml")

    assert workflow.count("SOURCE_SHA:") == 1
    assert "SOURCE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow


def test_all_ci_pytest_commands_suppress_clinical_failure_details() -> None:
    for relative in (
        ".github/workflows/agent-eval-gate.yml",
        ".github/workflows/agent-quality.yml",
        "agent/Makefile",
    ):
        commands = [
            line
            for line in _read(relative).splitlines()
            if "pytest" in line and not line.lstrip().startswith("#")
        ]
        assert commands
        for command in commands:
            assert "--tb=no" in command
            assert "--show-capture=no" in command


def test_bruno_cli_version_is_consistent_across_docs_and_ci() -> None:
    surfaces = [
        _read("agent/bruno/README.md"),
        _read("agent/bruno/mint-token.md"),
        _read(".github/workflows/agent-quality.yml"),
    ]
    combined = "\n".join(surfaces)
    assert "@usebruno/cli@3.5.1" not in combined
    assert combined.count("@usebruno/cli@3.5.2") >= 4
