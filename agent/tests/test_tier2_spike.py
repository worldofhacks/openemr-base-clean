"""W2-M24 — Tier-2 timing/cost/quota spike + fork-PR secret policy (frozen tests).

Frozen scope: AC-1..AC-6 (AC-7 is [live-measure] evidence, exempt per spec-lint).
All tests here are OFFLINE — no network, no provider calls, synthetic non-clinical
data only. Secret VALUES are never real: every "secret" in this file is an
obviously-fake fixture literal (FAKE markers throughout).

Module under test (NEW; does not exist at freeze time — RED by design):
    ops.spike_tier2

Frozen API surface these tests pin:

    extrapolate(units) -> dict
        Keys: "cases" (== 50), "projected_calls", "projected_seconds",
        "projected_input_tokens", "projected_output_tokens",
        "projected_cost_usd", "retry_amplification".
        Formula (W2-D8 / §7): 50 x the mean per-unit aggregate, where each
        unit is the three-call shape (VLM extraction + answer turn + judge
        turn), multi-page VLM calls counted explicitly (vlm "calls" == page
        count, one provider call per page), and retry_amplification ==
        total attempts / total base calls (attempts = calls + retries).
        Empty sample -> ValueError (never a silent zero projection).

    percentile(values, p) -> number
        Nearest-rank on observed values: rank = ceil(p/100 * n) (1-indexed
        into the sorted values, min rank 1). Observed-value discipline — a
        latency that never occurred is never reported (no interpolation).
        Empty input -> ValueError.

    build_report(units, *, rate_limit_headroom, daily_quota_statement,
                 max_cost_usd, max_seconds) -> dict
        The verdict and the W2-OA2 local-key substitution note are COMPUTED
        by the module (never caller-supplied — a caller-supplied verdict
        would be a self-grading report).

    render_report(report) -> str
        The report text surface — AC-4's no-secrets property applies here.

    lint_workflows(paths) -> list (empty == compliant)
        Violation = three-way conjunction (ticket Context, §6a):
        pull_request_target trigger AND checkout of PR-head code AND
        explicit secrets usage. Read-only over the real workflows.

    lint_policy_doc(path) -> list (empty == compliant)
        Checks the six frozen clauses of docs/week2/W2_TIER2_CI_POLICY.md.
        Missing file -> FileNotFoundError.

Measurement-record contract (plain dicts so tests carry no import-time
dependency on the module under test):

    unit = {"vlm": rec, "answer": rec, "judge": rec}
    rec  = {
        "calls": int,          # base provider calls needed; vlm: one per page
        "retries": int,        # extra attempts beyond the base calls
        "seconds": float,      # wall time actually spent, retries included
        "input_tokens": int,   # totals across all attempts
        "output_tokens": int,  # totals across all attempts
        "cost_usd": float,     # totals across all attempts
        # optional: "request_headers": dict — outbound header values (AC-4:
        # these must NEVER surface in the rendered report text)
    }
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Repo-root resolution: <root>/agent/tests/test_tier2_spike.py -> parents[2] == <root>
ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
POLICY_DOC = ROOT / "docs" / "week2" / "W2_TIER2_CI_POLICY.md"


def _spike():
    """Import the module under test lazily so each AC test RED-fails with
    ModuleNotFoundError (feature missing), not one opaque collection error."""
    return importlib.import_module("ops.spike_tier2")


# ---------------------------------------------------------------------------
# Canonical synthetic sample (hand-computed expectations used across AC-1/AC-3)
#
#   unit A: vlm 3 pages -> 3 calls (+1 retry), 6.0s, 3000/600 tok, $0.030
#           answer 1 call,             2.0s, 1200/300 tok, $0.012
#           judge  1 call,             1.0s,  800/100 tok, $0.006
#   unit B: vlm 1 page  -> 1 call,     2.0s, 1000/200 tok, $0.010
#           answer 1 call (+1 retry),  3.0s, 1500/400 tok, $0.015
#           judge  1 call,             1.0s,  700/100 tok, $0.005
#
#   base calls  = (3+1+1) + (1+1+1) = 8      attempts = 8 + 2 retries = 10
#   retry_amplification = 10 / 8 = 1.25
#   sum seconds = 9.0 + 6.0 = 15.0  -> mean 7.5   -> 50x = 375.0
#   sum in-tok  = 5000 + 3200 = 8200 -> mean 4100 -> 50x = 205000
#   sum out-tok = 1000 + 700 = 1700  -> mean 850  -> 50x = 42500
#   sum cost    = 0.048 + 0.030 = 0.078 -> mean 0.039 -> 50x = 1.95
#   attempts/unit mean = 5 -> projected_calls = 250 (== 50 x 4 base x 1.25)
# ---------------------------------------------------------------------------


def _rec(calls, retries, seconds, input_tokens, output_tokens, cost_usd, request_headers=None):
    rec = {
        "calls": calls,
        "retries": retries,
        "seconds": seconds,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    if request_headers is not None:
        rec["request_headers"] = dict(request_headers)
    return rec


def _canonical_units(request_headers=None):
    return [
        {
            "vlm": _rec(3, 1, 6.0, 3000, 600, 0.030, request_headers),
            "answer": _rec(1, 0, 2.0, 1200, 300, 0.012, request_headers),
            "judge": _rec(1, 0, 1.0, 800, 100, 0.006, request_headers),
        },
        {
            "vlm": _rec(1, 0, 2.0, 1000, 200, 0.010, request_headers),
            "answer": _rec(1, 1, 3.0, 1500, 400, 0.015, request_headers),
            "judge": _rec(1, 0, 1.0, 700, 100, 0.005, request_headers),
        },
    ]


def _build_report(mod, units=None, **overrides):
    kwargs = {
        "rate_limit_headroom": "synthetic: 4000 requests/min and 380000 input-tokens/min remaining",
        "daily_quota_statement": "synthetic: 50-case run fits comfortably inside the daily token quota",
        "max_cost_usd": 10.0,
        "max_seconds": 900.0,
    }
    kwargs.update(overrides)
    return mod.build_report(units if units is not None else _canonical_units(), **kwargs)


# ===========================================================================
# AC-1 — extrapolation formula: 50 x (VLM extraction + answer + judge)
# ===========================================================================


def test_extrapolation_matches_hand_computed_projection():
    # spec(W2-M24:AC-1)
    # guards: an extrapolator that "projects" anything other than the frozen
    # 50 x per-unit-aggregate formula (e.g. sums the sample, forgets the mean,
    # or quietly uses a different case count) sizing the W2-M20 gate wrong.
    mod = _spike()
    proj = mod.extrapolate(_canonical_units())
    assert proj["cases"] == 50
    assert proj["projected_calls"] == pytest.approx(250)
    assert proj["projected_seconds"] == pytest.approx(375.0)
    assert proj["projected_input_tokens"] == pytest.approx(205000)
    assert proj["projected_output_tokens"] == pytest.approx(42500)
    assert proj["projected_cost_usd"] == pytest.approx(1.95)
    assert proj["retry_amplification"] == pytest.approx(1.25)


def test_multi_page_vlm_calls_counted_explicitly_never_hidden_in_50_turns():
    # spec(W2-M24:AC-1)
    # guards: the W2-D8 trap this AC exists for — counting each unit as a flat
    # "3 calls" (hiding multi-page VLM extraction inside "50 turns") and
    # under-projecting quota/cost for the required gate.
    mod = _spike()
    unit = {
        "vlm": _rec(4, 0, 8.0, 4000, 800, 0.040),  # 4 pages -> 4 provider calls
        "answer": _rec(1, 0, 2.0, 1000, 250, 0.010),
        "judge": _rec(1, 0, 1.0, 500, 100, 0.005),
    }
    proj = mod.extrapolate([unit])
    # 50 x (4 + 1 + 1) = 300 — a lazy 3-calls-per-unit assumption yields 150.
    assert proj["projected_calls"] == pytest.approx(300)


def test_retry_amplification_is_exactly_one_when_no_retries_occurred():
    # spec(W2-M24:AC-1)
    # guards: a fudged amplification default (e.g. hardcoded 1.25 or 0) that
    # misstates a clean sample and corrupts the projected call budget.
    mod = _spike()
    unit = {
        "vlm": _rec(2, 0, 4.0, 2000, 400, 0.020),
        "answer": _rec(1, 0, 2.0, 1000, 250, 0.010),
        "judge": _rec(1, 0, 1.0, 500, 100, 0.005),
    }
    proj = mod.extrapolate([unit])
    assert proj["retry_amplification"] == pytest.approx(1.0)
    assert proj["projected_calls"] == pytest.approx(200)  # 50 x 4, unamplified


def test_extrapolation_rejects_an_empty_sample():
    # spec(W2-M24:AC-1)
    # guards: a divide-by-zero guard that silently returns a zero projection —
    # a $0.00 / 0s "measurement" would read as trivially viable and green-light
    # the gate on no data.
    mod = _spike()
    with pytest.raises(ValueError):
        mod.extrapolate([])


# ===========================================================================
# AC-2 — p50/p95 aggregation (nearest-rank; small samples and ties)
# ===========================================================================


def test_percentiles_match_hand_computed_values_on_unsorted_odd_sample():
    # spec(W2-M24:AC-2)
    # guards: an implementation that forgets to sort, or indexes off-by-one —
    # both silently misreport the latency distribution in the spike report.
    mod = _spike()
    values = [30.0, 10.0, 50.0, 20.0, 40.0]  # sorted: 10,20,30,40,50
    assert mod.percentile(values, 50) == pytest.approx(30.0)  # rank ceil(2.5)=3
    assert mod.percentile(values, 95) == pytest.approx(50.0)  # rank ceil(4.75)=5


def test_percentile_even_sample_uses_nearest_rank_not_interpolation():
    # spec(W2-M24:AC-2)
    # guards: swapping in statistics.median / linear interpolation, which
    # reports a value that was never observed (25.0 here) — nearest-rank on an
    # even sample is the frozen definition: rank ceil(0.5*4)=2 -> 20.0.
    mod = _spike()
    assert mod.percentile([10.0, 20.0, 30.0, 40.0], 50) == pytest.approx(20.0)


def test_percentiles_of_a_single_sample_collapse_to_that_value():
    # spec(W2-M24:AC-2)
    # guards: small-sample index arithmetic (rank 0 vs 1) crashing or
    # returning garbage on the spike's tiny working size.
    mod = _spike()
    assert mod.percentile([7.0], 50) == pytest.approx(7.0)
    assert mod.percentile([7.0], 95) == pytest.approx(7.0)


def test_percentiles_handle_tied_values():
    # spec(W2-M24:AC-2)
    # guards: tie-handling that dedupes values before ranking, which shifts
    # every percentile of a tied latency distribution.
    mod = _spike()
    values = [5.0, 5.0, 5.0, 9.0]
    assert mod.percentile(values, 50) == pytest.approx(5.0)  # rank ceil(2.0)=2
    assert mod.percentile(values, 95) == pytest.approx(9.0)  # rank ceil(3.8)=4


def test_p95_of_five_samples_is_the_maximum_observed():
    # spec(W2-M24:AC-2)
    # guards: interpolation quietly under-reporting the tail on the working
    # sample size (n=5): numpy-style linear p95 of this array is 80.8 — the
    # honest nearest-rank answer for a 5-sample is the observed max.
    mod = _spike()
    assert mod.percentile([1.0, 2.0, 3.0, 4.0, 100.0], 95) == pytest.approx(100.0)


def test_percentile_rejects_empty_input():
    # spec(W2-M24:AC-2)
    # guards: an empty measurement class silently reporting 0.0 latency
    # instead of failing loudly.
    mod = _spike()
    with pytest.raises(ValueError):
        mod.percentile([], 50)


# ===========================================================================
# AC-3 — report shape: every required field, closed-set verdict, W2-OA2 note
# ===========================================================================


def test_report_contains_every_required_field():
    # spec(W2-M24:AC-3)
    # guards: a report missing any field the ticket report / W2-M20 sizing
    # depends on (headroom, quota statement, budget, amplification, note) —
    # absence would only be noticed at live-measure time, after freeze.
    mod = _spike()
    report = _build_report(mod)

    assert report["sample_size"] == 2

    per_class = report["per_call_class"]
    assert set(per_class.keys()) == {"vlm", "answer", "judge"}
    for stats in per_class.values():
        for field in ("p50_seconds", "p95_seconds", "input_tokens", "output_tokens", "cost_usd"):
            assert field in stats

    assert report["retry_amplification"] == pytest.approx(1.25)
    assert report["rate_limit_headroom"].startswith("synthetic: 4000 requests/min")
    assert report["daily_quota_statement"].startswith("synthetic: 50-case run fits")

    extrapolated = report["extrapolated_50"]
    assert extrapolated["cases"] == 50
    assert extrapolated["projected_cost_usd"] == pytest.approx(1.95)
    assert extrapolated["projected_seconds"] == pytest.approx(375.0)

    assert report["budget"]["max_cost_usd"] == pytest.approx(10.0)
    assert report["budget"]["max_seconds"] == pytest.approx(900.0)

    # Closed set — anything else ("maybe", "PASS", None) is a spec violation.
    assert report["verdict"] in {"viable", "stop_escalate"}

    # W2-OA2 local-key substitution note: computed by the module, references
    # the pending owner action by id, and is a real sentence, not "".
    note = report["local_key_substitution_note"]
    assert isinstance(note, str)
    assert note.strip()
    assert "W2-OA2" in note


def test_report_per_call_class_stats_are_hand_verified():
    # spec(W2-M24:AC-3)
    # guards: per-class stats computed over the wrong axis (pooling all
    # classes together, or reporting per-call instead of per-unit-class
    # totals) — the per-class split is what W2-M20 uses to budget each leg.
    mod = _spike()
    per_class = _build_report(mod)["per_call_class"]

    # vlm seconds across units: [6.0, 2.0] -> nearest-rank p50=2.0, p95=6.0
    assert per_class["vlm"]["p50_seconds"] == pytest.approx(2.0)
    assert per_class["vlm"]["p95_seconds"] == pytest.approx(6.0)
    assert per_class["vlm"]["input_tokens"] == 4000
    assert per_class["vlm"]["output_tokens"] == 800
    assert per_class["vlm"]["cost_usd"] == pytest.approx(0.040)

    # answer seconds: [2.0, 3.0] -> p50=2.0, p95=3.0
    assert per_class["answer"]["p50_seconds"] == pytest.approx(2.0)
    assert per_class["answer"]["p95_seconds"] == pytest.approx(3.0)
    assert per_class["answer"]["input_tokens"] == 2700
    assert per_class["answer"]["cost_usd"] == pytest.approx(0.027)

    assert per_class["judge"]["cost_usd"] == pytest.approx(0.011)


def test_verdict_is_viable_when_projection_fits_both_budgets():
    # spec(W2-M24:AC-3)
    # guards: a verdict hardcoded to stop_escalate (or derived from the
    # sample instead of the 50-case projection) blocking a gate that fits.
    mod = _spike()
    # Projection: $1.95 / 375.0s vs budget $10.00 / 900.0s -> fits.
    report = _build_report(mod, max_cost_usd=10.0, max_seconds=900.0)
    assert report["verdict"] == "viable"


def test_verdict_is_stop_escalate_when_projected_cost_exceeds_budget():
    # spec(W2-M24:AC-3)
    # guards: a verdict hardcoded to "viable" — the locked decision says a
    # failing fit is a STOP escalation, never absorbed silently.
    mod = _spike()
    # Projection: $1.95 vs $1.00 budget -> over cost budget.
    report = _build_report(mod, max_cost_usd=1.0, max_seconds=900.0)
    assert report["verdict"] == "stop_escalate"


def test_verdict_is_stop_escalate_when_projected_runtime_exceeds_budget():
    # spec(W2-M24:AC-3)
    # guards: a cost-only viability check — runtime is an independent budget
    # axis (a PR-blocking gate that takes hours fails even if cheap).
    mod = _spike()
    # Projection: 375.0s vs 300.0s budget -> over time budget, cost fits.
    report = _build_report(mod, max_cost_usd=10.0, max_seconds=300.0)
    assert report["verdict"] == "stop_escalate"


# ===========================================================================
# AC-4 — no key material / Authorization header values in the report text
# (property-style over adversarially seeded, OBVIOUSLY FAKE secrets)
# ===========================================================================

FAKE_KEYS = [
    "sk-ant-FAKE-fixture-000",
    "sk-ant-api03-FAKE-0000000000",
    "FAKE-OPAQUE-TOKEN-never-print-me",
]


@pytest.mark.parametrize("fake_key", FAKE_KEYS)
def test_report_text_contains_no_key_material_or_auth_header_values(monkeypatch, fake_key):
    # spec(W2-M24:AC-4)
    # guards: the report/renderer echoing the env key or outbound request
    # headers (the classic "dump config for debugging" leak) — the spike
    # report is committed as ticket evidence, so one leak is a burned key.
    mod = _spike()
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_key)
    auth_header = f"Bearer {fake_key}"
    units = _canonical_units(
        request_headers={"authorization": auth_header, "x-api-key": fake_key}
    )
    text = mod.render_report(_build_report(mod, units=units))

    assert isinstance(text, str) and text.strip()
    # The note must reach the text surface (env var NAMES are fine; values never).
    assert "W2-OA2" in text
    assert fake_key not in text
    assert auth_header not in text
    assert "sk-ant-" not in text


def test_report_text_leaks_no_env_secret_values_even_across_multiple_vars(monkeypatch):
    # spec(W2-M24:AC-4)
    # guards: a renderer sanitized only for ANTHROPIC_API_KEY while another
    # env secret (client secret, DSN password) rides along into the text.
    mod = _spike()
    fake_env = {
        "ANTHROPIC_API_KEY": "sk-ant-FAKE-fixture-000",
        "SMART_CLIENT_SECRET": "FAKE-smart-client-secret-111",
        "SESSION_STORE_DSN": "postgresql://fakeuser:FAKE-db-pass-222@db.invalid:5432/agent",
    }
    for key, value in fake_env.items():
        monkeypatch.setenv(key, value)

    units = _canonical_units(
        request_headers={"authorization": "Bearer sk-ant-FAKE-fixture-000"}
    )
    text = mod.render_report(_build_report(mod, units=units))

    assert isinstance(text, str) and text.strip()
    for value in fake_env.values():
        assert value not in text
    assert "FAKE-db-pass-222" not in text  # the DSN's password segment specifically


# ===========================================================================
# AC-5 — workflow policy lint (read-only over the real .github/workflows/)
# ===========================================================================

# Synthetic VIOLATING workflow: pull_request_target + checkout of PR-head code
# + explicit secrets usage — the full three-way conjunction. Never written to
# .github/workflows/; tmp_path only. Secrets referenced are FAKE names.
_VIOLATING_WORKFLOW = """\
name: synthetic violating fixture (test-only, never installed)
on:
  pull_request_target:
    branches: [master]
jobs:
  exfiltrable:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: __REF_EXPR__
      - name: run fork-controlled code next to a secret
        run: ./ci/build.sh
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
"""

# Compliant: pull_request_target + secrets, but checkout has NO ref override —
# under pull_request_target the default checkout is the BASE repo's code, not
# the fork's. Flagging this would red legitimate trusted-base workflows.
_COMPLIANT_BASE_CHECKOUT = """\
name: compliant fixture — base-repo checkout under pull_request_target
on:
  pull_request_target:
jobs:
  label:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: run trusted base-repo script
        run: ./scripts/label.sh
        env:
          APP_KEY: ${{ secrets.FAKE_APP_KEY }}
"""

# Compliant: plain pull_request trigger — fork PRs get no repository secrets in
# this context, so head-ref checkout + a secrets reference is not the policy
# violation (the conjunction requires the pull_request_target trigger).
_COMPLIANT_PULL_REQUEST_TRIGGER = """\
name: compliant fixture — pull_request trigger with head checkout
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - name: run tests
        run: make test
        env:
          COVERAGE_TOKEN: ${{ secrets.FAKE_COVERAGE_TOKEN }}
"""


def _real_workflow_paths():
    paths = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    return paths


def test_existing_real_workflows_pass_the_policy_lint():
    # spec(W2-M24:AC-5)
    # guards: an over-broad lint (e.g. "pull_request_target + secrets =
    # violation") permanently redding this frozen test against workflow files
    # the ticket is forbidden to modify.
    mod = _spike()
    paths = _real_workflow_paths()
    assert len(paths) >= 20, "workflow glob found suspiciously few files — wrong repo root?"
    assert any(p.name == "dependabot-auto-merge.yml" for p in paths)
    assert list(mod.lint_workflows(paths)) == []


@pytest.mark.parametrize(
    "ref_expr",
    [
        "${{ github.event.pull_request.head.sha }}",
        "${{ github.event.pull_request.head.ref }}",
        "${{ github.head_ref }}",
    ],
)
def test_lint_fires_on_pull_request_target_head_checkout_with_secrets(tmp_path, ref_expr):
    # spec(W2-M24:AC-5)
    # guards: a lint that only pattern-matches one literal head-ref spelling
    # (or a vacuous lint returning [] for everything) missing the actual
    # secret-exfiltration shape §6a forbids.
    mod = _spike()
    violating = tmp_path / "violating_workflow.yml"
    violating.write_text(_VIOLATING_WORKFLOW.replace("__REF_EXPR__", ref_expr))
    compliant = tmp_path / "compliant_workflow.yml"
    compliant.write_text(_COMPLIANT_PULL_REQUEST_TRIGGER)

    violations = list(mod.lint_workflows([violating, compliant]))
    assert len(violations) >= 1
    # The finding must identify the offending file — and only that file.
    assert "violating_workflow.yml" in str(violations)
    assert "compliant_workflow.yml" not in str(violations)


def test_dependabot_auto_merge_near_miss_passes_the_lint():
    # spec(W2-M24:AC-5)
    # guards: the exact naively-over-broad failure the ticket names —
    # dependabot-auto-merge.yml uses pull_request_target AND secrets but never
    # checks out PR code, so the three-way conjunction is not satisfied.
    mod = _spike()
    near_miss = WORKFLOWS_DIR / "dependabot-auto-merge.yml"
    assert near_miss.exists()
    text = near_miss.read_text()
    # Premise check: the near-miss is still a near-miss (trigger + secrets,
    # no PR-code checkout). If this workflow changes shape upstream, the
    # failure points here rather than at the lint.
    assert "pull_request_target" in text
    assert "secrets." in text

    assert list(mod.lint_workflows([near_miss])) == []


@pytest.mark.parametrize(
    "content, why_compliant",
    [
        (_COMPLIANT_BASE_CHECKOUT, "checkout without ref under pull_request_target is base-repo code"),
        (_COMPLIANT_PULL_REQUEST_TRIGGER, "plain pull_request trigger is not pull_request_target"),
    ],
    ids=["base_ref_checkout_under_pull_request_target", "plain_pull_request_trigger"],
)
def test_lint_is_not_over_broad_on_compliant_synthetic_patterns(tmp_path, content, why_compliant):
    # spec(W2-M24:AC-5)
    # guards: a two-way lint (trigger+secrets, or checkout+secrets) that
    # would false-positive on legitimate patterns — the ticket requires the
    # full trigger + PR-head-checkout + secrets conjunction to fire.
    mod = _spike()
    workflow = tmp_path / "compliant_workflow.yml"
    workflow.write_text(content)
    assert list(mod.lint_workflows([workflow])) == [], why_compliant


# ===========================================================================
# AC-6 — policy-doc lint over docs/week2/W2_TIER2_CI_POLICY.md (six clauses)
# ===========================================================================


def test_policy_doc_exists_and_passes_the_clause_lint():
    # spec(W2-M24:AC-6)
    # guards: W2-M20 consuming a policy doc that silently dropped one of the
    # six frozen clauses (RED now for the right reason: the doc — an
    # implementation deliverable of this ticket — does not exist yet).
    mod = _spike()
    assert POLICY_DOC.exists(), f"frozen policy doc missing: {POLICY_DOC}"
    assert list(mod.lint_policy_doc(POLICY_DOC)) == []


def test_policy_doc_states_the_signature_terms_of_every_frozen_clause():
    # spec(W2-M24:AC-6)
    # guards: a lint + doc pair that agree on six meaningless markers —
    # this independent content floor checks the real doc's own text for
    # terms an honest statement of each clause cannot avoid.
    assert POLICY_DOC.exists(), f"frozen policy doc missing: {POLICY_DOC}"
    text = POLICY_DOC.read_text().lower().replace("-", " ")
    for term in (
        "fork",                  # clause 1: no repository secrets to forks
        "secret",                # clauses 1 & 5
        "pull_request_target",   # clause 2: never checkout fork code under it
        "tier 1",                # clause 3: forks run Tier 1 only
        "tier 2",                # clause 4: maintainer reproduces for Tier-2 result
        "maintainer",            # clause 4
        "commit",                # clause 4: exact fork commit; new commit invalidates
        "least privilege",       # clause 5: least-privilege environments
        "artifact",              # clause 5: no secret echo / artifact retention
        "escalat",               # clause 6: STOP escalation, never...
        "50",                    # clause 6: ...a reduction of the 50 cases
    ):
        assert term in text, f"policy doc never states clause term: {term!r}"


def test_policy_lint_fires_on_a_stub_doc_missing_all_clauses(tmp_path):
    # spec(W2-M24:AC-6)
    # guards: a lint that only checks file existence (or fewer than six
    # clauses) — a stub stating nothing must surface all six as findings.
    mod = _spike()
    stub = tmp_path / "stub_policy.md"
    stub.write_text("# W2 Tier-2 CI policy\n\nTBD.\n")
    violations = list(mod.lint_policy_doc(stub))
    assert len(violations) >= 6


def test_policy_lint_raises_on_missing_file():
    # spec(W2-M24:AC-6)
    # guards: a missing policy doc reading as "no violations" — absence must
    # fail loudly, not pass silently.
    mod = _spike()
    with pytest.raises(FileNotFoundError):
        mod.lint_policy_doc(POLICY_DOC.parent / "DOES_NOT_EXIST_W2_TIER2_CI_POLICY.md")
