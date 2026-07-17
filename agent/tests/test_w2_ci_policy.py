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
    assert "  actions: read" in workflow
    assert "  checks: read" in workflow
    assert "  contents: read" in workflow
    assert "W2_EVAL_NETWORK: disabled" in workflow
    assert "COHERE_API_KEY: ''" in workflow
    assert "needs: [quality, eval-tier1]" in workflow
    assert workflow.count("SOURCE_SHA:") == 2
    assert workflow.count("github.event.pull_request.head.sha") >= 3
    assert workflow.count("python -m evals.artifact_scan") == 2
    assert workflow.count("if: success()") == 1
    assert workflow.index("--eval-result evals/results-tier1.json") < (
        workflow.index("name: eval-results-tier1")
    )
    assert workflow.index('--eval-result "$RUNNER_TEMP/results-tier2.json"') < (
        workflow.index("name: eval-results-tier2-live")
    )
    live_job = workflow.split("  eval-tier2-live:", maxsplit=1)[1]
    live_condition = live_job.split("    runs-on:", maxsplit=1)[0]
    assert "github.event_name == 'pull_request'" not in live_condition
    assert "github.event_name == 'push'" in live_condition
    assert "github.ref == 'refs/heads/main'" in live_condition
    assert "id: live-gate" in live_job
    assert "continue-on-error: true" in live_job
    assert "if: always() && steps.live-scan.outcome == 'success'" in live_job
    assert "Enforce live gate, scan, and retention outcomes" in live_job
    assert 'test "$LIVE_OUTCOME" = success' in live_job
    assert 'test "$SCAN_OUTCOME" = success' in live_job
    assert 'test "$UPLOAD_OUTCOME" = success' in live_job
    regression = next(
        line for line in workflow.splitlines() if "pytest tests evals" in line
    )
    assert "--tb=no" in regression
    assert "--show-capture=no" in regression


def test_main_reuses_exact_sha_live_attestation_without_a_second_provider_call() -> None:
    workflow = _read(".github/workflows/agent-eval-gate.yml")
    live_job = workflow.split("  eval-tier2-live:", maxsplit=1)[1]
    reuse = live_job.index("Reuse the exact-SHA green full-live result on main")
    require_key = live_job.index("Require protected provider key")
    full_live = live_job.index("Full live 50-case gate")

    assert reuse < require_key < full_live
    assert "python ../.github/scripts/reuse_live_eval.py" in live_job
    assert '--sha "$EVALUATED_SHA"' in live_job
    assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in live_job
    main_only = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    paid_only = "github.event_name != 'push' || github.ref != 'refs/heads/main'"
    assert live_job.count(main_only) == 1
    assert live_job.count(paid_only) == 2
    assert 'test "$REUSED" = true' in live_job
    assert 'test "$LIVE_OUTCOME" = skipped' in live_job
    assert 'test "$LIVE_OUTCOME" = success' in live_job


def test_new_agent_workflows_pin_third_party_actions_to_commits() -> None:
    for relative in (
        ".github/workflows/agent-eval-gate.yml",
        ".github/workflows/agent-eval-live-subset.yml",
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
    lock = json.loads(_read(".github/railway-cli/package-lock.json"))
    install = workflow.split(
        "- name: Install locked Railway CLI without deployment credentials",
        maxsplit=1,
    )[1].split("- name: Configure exact source identity", maxsplit=1)[0]

    assert "npx" not in workflow
    assert "RAILWAY_TOKEN" not in install
    assert "npm ci --ignore-scripts --prefix .github/railway-cli" in install
    assert "npm rebuild" not in install
    assert "npm run" not in install
    assert "npm install" not in install
    assert re.search(r"curl[^\n]*\|\s*(?:ba)?sh", install) is None
    assert "railway-v5.26.1-x86_64-unknown-linux-gnu.tar.gz" in install
    assert "7ab32701c4da05eafd0e2a956b441bea581d61def9a0689d9c35d2759e6f3640" in install
    assert 'test "$(tar -tzf "$archive")" = "railway"' in install
    assert 'test ! -L "$cli_dir/railway"' in install
    assert 'railway --version)" = "railway 5.26.1"' in install
    assert install.index("npm ci --ignore-scripts") < install.index("curl --proto")
    assert install.index("curl --proto") < install.index("sha256sum -c -")
    assert install.index("sha256sum -c -") < install.index("tar -xzf")
    assert install.index("tar -xzf") < install.index("railway --version")
    assert install.index("railway --version") < install.index("npm audit")
    assert workflow.index("npm audit") < workflow.index("RAILWAY_TOKEN")
    assert package["dependencies"]["@railway/cli"] == "5.26.1"
    assert package["overrides"]["tar"] == "7.5.20"
    cli_lock = lock["packages"]["node_modules/@railway/cli"]
    tar_lock = lock["packages"]["node_modules/tar"]
    assert cli_lock["version"] == "5.26.1"
    assert cli_lock["integrity"] == (
        "sha512-fMue1EcqsBldpvataux1lT6Fp68MqvurAQNEy2ZfNQLaikgfz2VwpU/"
        "QSA5qs9HYQsC1QuTr4O5svL/sUmIcPA=="
    )
    assert tar_lock["version"] == "7.5.20"
    assert (_ROOT / ".github/railway-cli/package-lock.json").is_file()


def test_deploy_uses_an_exact_sha_context_and_explicit_worker_config() -> None:
    workflow = _read(".github/workflows/agent-deploy.yml")

    exact_context = 'git diff --quiet "$EVALUATED_SHA" -- agent'
    tracked_files = "git ls-files -z -- agent"
    tracked_copy = (
        "tar --create --file=- --null --verbatim-files-from "
        "--no-recursion --files-from=-"
    )
    required_worker_module = 'test -f "$worker_context/app/tools/__init__.py"'
    required_worker_tool = 'test -f "$worker_context/app/tools/fhir_tools.py"'
    worker_config = (
        'cp "$worker_context/railway.worker.json" '
        '"$worker_context/railway.json"'
    )
    worker_deploy = '"$railway" up --service document-worker --ci'
    web_deploy = '"$railway" up --service agent --ci'

    assert "git archive" not in workflow
    assert workflow.count(web_deploy) == 1
    assert "--detach" not in workflow
    assert workflow.count(worker_deploy) == 1
    assert "set -o pipefail" in workflow
    assert exact_context in workflow
    assert tracked_files in workflow
    assert tracked_copy in workflow
    assert '--strip-components=1' in workflow
    assert required_worker_module in workflow
    assert required_worker_tool in workflow
    assert worker_config in workflow
    assert '"deploy"]["startCommand"]' in workflow
    assert (
        workflow.index(exact_context)
        < workflow.index(tracked_files)
        < workflow.index(required_worker_module)
        < workflow.index(required_worker_tool)
        < workflow.index(worker_config)
        < workflow.index(worker_deploy)
    )
    assert workflow.index(worker_deploy) < workflow.index(web_deploy)
    assert worker_deploy + " --detach" not in workflow
    assert workflow.count('set "DEPLOYMENT_SHA=$EVALUATED_SHA" --skip-deploys') == 2
    assert "python agent/scripts/verify_deployed_sha.py" in workflow


def test_gitlab_gate_scans_before_success_only_artifact_retention() -> None:
    pipeline = _read(".gitlab-ci.yml")

    assert "python -m evals.w2_runner run --tier recorded" in pipeline
    assert "--eval-result evals/results-tier1.json" in pipeline
    assert "when: on_success" in pipeline
    assert "when: always" not in pipeline
    bridge = pipeline.split("github-exact-sha-bridge:", maxsplit=1)[1]
    assert "before_script: []" in bridge
    assert bridge.index("before_script: []") < bridge.index("verify_github_gate.py")


def test_quality_recorded_gate_binds_pr_head_sha_explicitly() -> None:
    workflow = _read(".github/workflows/agent-quality.yml")

    assert workflow.count("SOURCE_SHA:") == 1
    assert "SOURCE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow


def test_live_subset_workflow_is_exact_sha_bounded_and_non_gating() -> None:
    workflow = _read(".github/workflows/agent-eval-live-subset.yml")
    candidates = (_ROOT / "agent/evals/citation-regression-candidates.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    golden = json.loads(_read("agent/evals/golden/cases.json"))
    high_citation_cases = [
        case["case_id"] for case in golden if len(case["expected_citations"]) >= 8
    ]

    assert workflow.startswith("name: agent-eval-live-subset\n")
    assert "'tier2-subset/**'" in workflow
    assert "environment: eval-tier2-live" in workflow
    assert "name: diagnostic-live-subset" in workflow
    assert "name: eval-tier2-live" not in workflow
    assert "python -m evals.w2_runner diagnose-live" in workflow
    assert "run --tier live" not in workflow
    assert "--max-cost-usd 5" in workflow
    assert "--max-seconds 1200" in workflow
    assert '--eval-result "$RUNNER_TEMP/results-live-subset.json"' in workflow
    assert "eval-results-tier2-live-subset" in workflow
    assert "name: eval-results-tier2-live\n" not in workflow
    assert 'test "${#selected[@]}" -le 20' in workflow
    assert 'test "$expected" = "$GITHUB_SHA"' in workflow
    assert 'test "$expected" = "$actual"' in workflow
    assert candidates == high_citation_cases
    assert len(candidates) == len(set(candidates)) == 19


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
