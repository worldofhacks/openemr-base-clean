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


# ===========================================================================
# AC-5 (additions) — lint evasion cases being CLOSED (security finding).
#
# The frozen AC-5 lint above detects the three-way conjunction only for a
# narrow set of spellings. Two evasion families defeat it while checking out
# the exact same attacker-controlled PR code next to a live secret:
#
#   (a) EQUIVALENT PR-HEAD-REF SPELLINGS — the checkout leg keys on
#       github.head_ref / pull_request.head.{sha,ref}, but policy clause 2
#       covers "any equivalent spelling". A ref of "refs/pull/<n>/head",
#       "refs/pull/<n>/merge", or github.event.pull_request.merge_commit_sha
#       all check out fork-controlled PR code just the same, and slip past a
#       literal-marker matcher.
#
#   (b) THE IMPLICIT WRITE-CAPABLE GITHUB_TOKEN — the secret-usage leg keys on
#       ${{ secrets.* }} / "secrets: inherit". But under pull_request_target
#       every job implicitly holds a write-capable GITHUB_TOKEN reachable as
#       ${{ github.token }}. PR-head checkout + github.token usage completes
#       the exfiltration shape with no "secrets." spelled anywhere.
#
# These tests are RED against the current lint (evasion undetected -> no
# violation reported where one is required). The compliant near-miss
# dependabot-auto-merge.yml (pull_request_target + secrets, NO PR-code
# checkout) must STILL pass after the lint is hardened. All fixtures are
# tmp_path only, never written to .github/workflows/, FAKE secret names only.
# ===========================================================================

# Equivalent PR-head-ref spellings that still check out attacker PR code but
# avoid the github.head_ref / pull_request.head.{sha,ref} literals.
_EQUIVALENT_PR_HEAD_REFS = [
    "refs/pull/${{ github.event.pull_request.number }}/head",
    "refs/pull/${{ github.event.pull_request.number }}/merge",
    "${{ github.event.pull_request.merge_commit_sha }}",
]

# Violating: pull_request_target + PR-head checkout via ${{ github.head_ref }}
# (a spelling the current checkout leg ALREADY recognizes) + the write-capable
# implicit GITHUB_TOKEN as ${{ github.token }}, with NO "secrets." anywhere.
# This isolates the implicit-token leg: trigger and checkout are already
# detectable, so the only reason the current lint stays silent is that its
# secret-usage leg never matches github.token.
_IMPLICIT_GITHUB_TOKEN_WORKFLOW = """\
name: synthetic implicit-token violation (test-only, never installed)
on:
  pull_request_target:
    branches: [master]
jobs:
  exfiltrable:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.head_ref }}
      - name: use the implicit write-capable token against fork PR code
        run: gh pr merge --auto "$PR_URL"
        env:
          GITHUB_TOKEN: ${{ github.token }}
"""


@pytest.mark.parametrize(
    "ref_expr",
    _EQUIVALENT_PR_HEAD_REFS,
    ids=["refs_pull_number_head", "refs_pull_number_merge", "merge_commit_sha"],
)
def test_lint_fires_on_equivalent_pr_head_ref_spellings_with_secrets(tmp_path, ref_expr):
    # spec(W2-M24:AC-5)
    # guards: a checkout leg that only matches github.head_ref /
    # pull_request.head.{sha,ref} — policy clause 2 covers "any equivalent
    # spelling", and refs/pull/<n>/head, refs/pull/<n>/merge, and
    # merge_commit_sha each check out attacker-controlled PR code just the
    # same. RED today: these spellings are unrecognized, so the three-way
    # conjunction never fires and the exfiltration shape passes unflagged.
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


def test_lint_fires_on_implicit_github_token_under_pull_request_target(tmp_path):
    # spec(W2-M24:AC-5)
    # guards: a secret-usage leg that only matches ${{ secrets.* }} /
    # "secrets: inherit" — under pull_request_target every job holds a
    # write-capable GITHUB_TOKEN reachable as ${{ github.token }}, so a
    # PR-head checkout + github.token usage exfiltrates with NO "secrets."
    # anywhere. RED today: the secrets leg misses github.token entirely, so
    # the conjunction stays silent on a genuinely dangerous workflow.
    mod = _spike()
    # Premise: the fixture never spells "secrets." — the leg the current lint
    # keys on is genuinely absent, so a firing lint MUST detect github.token.
    assert "secrets." not in _IMPLICIT_GITHUB_TOKEN_WORKFLOW
    assert "${{ github.token }}" in _IMPLICIT_GITHUB_TOKEN_WORKFLOW

    violating = tmp_path / "violating_workflow.yml"
    violating.write_text(_IMPLICIT_GITHUB_TOKEN_WORKFLOW)

    violations = list(mod.lint_workflows([violating]))
    assert len(violations) >= 1
    assert "violating_workflow.yml" in str(violations)


def test_dependabot_near_miss_still_passes_after_evasion_hardening():
    # spec(W2-M24:AC-5)
    # guards: the risk that closing the two evasion gaps above over-broadens
    # the lint and reds the compliant near-miss the ticket is forbidden to
    # modify — dependabot-auto-merge.yml is pull_request_target + secrets but
    # checks out NO PR code in any spelling, so the three-way conjunction must
    # stay unsatisfied. GREEN now and MUST stay GREEN after the lint is
    # hardened for the equivalent-ref and implicit-token legs.
    mod = _spike()
    near_miss = WORKFLOWS_DIR / "dependabot-auto-merge.yml"
    assert near_miss.exists()
    text = near_miss.read_text()
    # Premise: still pull_request_target + secrets, and no PR-head checkout in
    # ANY equivalent spelling (nor the implicit-token path) — so hardening the
    # detector for those spellings must not change this workflow's verdict.
    assert "pull_request_target" in text
    assert "secrets." in text
    for spelling in _EQUIVALENT_PR_HEAD_REFS + [
        "${{ github.head_ref }}",
        "${{ github.event.pull_request.head.sha }}",
        "${{ github.event.pull_request.head.ref }}",
        "${{ github.token }}",
    ]:
        assert spelling not in text, f"near-miss unexpectedly contains {spelling!r}"

    assert list(mod.lint_workflows([near_miss])) == []


# ===========================================================================
# Fresh review additions — quota fail-closed behavior, equivalent
# pwn-request workflow shapes, and the binding W2-D8 live-call configuration.
#
# These tests intentionally use structured status/classification contracts.
# Verdicts must not be inferred by searching human-readable quota prose, and
# workflow findings must distinguish executable PR-head checkout shapes from
# comments, echoed examples, and base-ref checkouts.
# ===========================================================================


def test_viability_accepts_structured_daily_and_spend_quota_sufficiency():
    # spec(W2-M24:AC-3; W2-D8/§7 quota decision guard)
    # guards: a fail-closed repair that can never emit viable, even when both
    # independent quota axes are explicitly sufficient and the cost/runtime
    # projection fits. The approved legacy positive-string fixture above
    # remains compatible; this is the new machine-readable positive control.
    mod = _spike()
    quota_evidence = {
        "statement": "synthetic opaque quota evidence",
        "sufficiency": {"daily": "sufficient", "spend": "sufficient"},
    }
    report = _build_report(
        mod,
        daily_quota_statement=quota_evidence,
        max_cost_usd=10.0,
        max_seconds=900.0,
    )

    assert report["daily_quota_statement"] == quota_evidence
    assert report["extrapolated_50"]["projected_cost_usd"] < 10.0
    assert report["extrapolated_50"]["projected_seconds"] < 900.0
    assert report["verdict"] == "viable"


@pytest.mark.parametrize(
    "max_cost_usd,max_seconds,overrun_axis",
    [
        pytest.param(1.0, 900.0, "cost", id="cost_over_budget"),
        pytest.param(10.0, 300.0, "runtime", id="runtime_over_budget"),
    ],
)
def test_structured_sufficient_quota_still_enforces_cost_and_runtime_budgets(
    max_cost_usd, max_seconds, overrun_axis
):
    # spec(W2-M24:AC-3; W2-D8/§7 quota decision guard)
    # guards: a new structured-quota branch returning viable immediately and
    # bypassing the independent cost/runtime STOP conditions.
    mod = _spike()
    quota_evidence = {
        "statement": "synthetic opaque quota evidence",
        "sufficiency": {"daily": "sufficient", "spend": "sufficient"},
    }
    report = _build_report(
        mod,
        daily_quota_statement=quota_evidence,
        max_cost_usd=max_cost_usd,
        max_seconds=max_seconds,
    )

    projection = report["extrapolated_50"]
    if overrun_axis == "cost":
        assert projection["projected_cost_usd"] > max_cost_usd
        assert projection["projected_seconds"] < max_seconds
    else:
        assert projection["projected_seconds"] > max_seconds
        assert projection["projected_cost_usd"] < max_cost_usd
    assert report["daily_quota_statement"] == quota_evidence
    assert report["verdict"] == "stop_escalate"


@pytest.mark.parametrize(
    "quota_evidence",
    [
        pytest.param(None, id="missing_evidence"),
        pytest.param("synthetic opaque quota evidence", id="unstructured_opaque_prose"),
        pytest.param(
            "synthetic quota fits and both axes are sufficient",
            id="deceptive_viability_words",
        ),
        pytest.param(
            {"statement": "synthetic opaque quota evidence"},
            id="missing_sufficiency_object",
        ),
        pytest.param(
            {
                "statement": "synthetic opaque quota evidence",
                "sufficiency": {"spend": "sufficient"},
            },
            id="missing_daily_status",
        ),
        pytest.param(
            {
                "statement": "synthetic opaque quota evidence",
                "sufficiency": {"daily": "sufficient"},
            },
            id="missing_spend_status",
        ),
        pytest.param(
            {
                "statement": "synthetic opaque quota evidence",
                "sufficiency": "daily=sufficient, spend=sufficient",
            },
            id="unstructured_sufficiency_value",
        ),
    ],
)
def test_viability_fails_closed_on_missing_or_unstructured_quota_evidence(
    quota_evidence,
):
    # spec(W2-M24:AC-3; W2-D8/§7 quota decision guard)
    # guards: missing/malformed quota evidence silently inheriting viability
    # from cost and runtime. Arbitrary prose is deliberately opaque; this does
    # not invalidate the approved freeze's specific legacy positive statement.
    mod = _spike()
    report = _build_report(
        mod,
        daily_quota_statement=quota_evidence,
        max_cost_usd=10.0,
        max_seconds=900.0,
    )

    assert report["extrapolated_50"]["projected_cost_usd"] < 10.0
    assert report["extrapolated_50"]["projected_seconds"] < 900.0
    assert report["daily_quota_statement"] == quota_evidence
    assert report["verdict"] == "stop_escalate"


@pytest.mark.parametrize(
    "daily_status,spend_status",
    [
        ("unknown", "sufficient"),
        ("insufficient", "sufficient"),
        ("sufficient", "unknown"),
        ("sufficient", "insufficient"),
        ("Sufficient", "sufficient"),
        ("sufficient", "SUFFICIENT"),
        (True, "sufficient"),
        ("sufficient", 1),
        (None, "sufficient"),
        ("sufficient", None),
    ],
    ids=[
        "daily_unknown",
        "daily_insufficient",
        "spend_unknown",
        "spend_insufficient",
        "daily_wrong_case",
        "spend_wrong_case",
        "daily_wrong_type",
        "spend_wrong_type",
        "daily_null",
        "spend_null",
    ],
)
def test_viability_fails_closed_on_structured_daily_or_spend_quota_status(
    daily_status, spend_status
):
    # spec(W2-M24:AC-3; W2-D8/§7 quota decision guard)
    # guards: declaring the required live gate viable from runtime/cost alone
    # while daily provider capacity or the account spend quota is unknown or
    # insufficient. The narrative is deliberately opaque: only the structured
    # status is authoritative, so a keyword search cannot self-grade the run.
    mod = _spike()
    quota_evidence = {
        "statement": "synthetic opaque quota evidence",
        "sufficiency": {"daily": daily_status, "spend": spend_status},
    }
    report = _build_report(
        mod,
        daily_quota_statement=quota_evidence,
        max_cost_usd=10.0,
        max_seconds=900.0,
    )

    # Premise: the same sample fits both per-run budgets; quota is the sole
    # reason the locked decision must stop-escalate.
    assert report["extrapolated_50"]["projected_cost_usd"] < 10.0
    assert report["extrapolated_50"]["projected_seconds"] < 900.0
    assert report["daily_quota_statement"] == quota_evidence
    assert report["verdict"] == "stop_escalate"


def test_per_minute_headers_do_not_claim_daily_or_spend_quota_sufficiency():
    # spec(W2-M24:AC-3; W2-D8/§7 quota decision guard)
    # guards: treating Anthropic per-minute rate limits as proof that a daily
    # run or account spend quota fits. Neither capacity is present in these
    # headers, so both structured statuses must remain unknown and viability
    # must fail closed even though cost/runtime fit.
    mod = _spike()
    projection = mod.extrapolate(_canonical_units())
    evidence = mod._daily_quota_statement(
        {
            "anthropic-ratelimit-requests-limit": "4000",
            "anthropic-ratelimit-input-tokens-limit": "400000",
            "anthropic-ratelimit-output-tokens-limit": "80000",
        },
        projection,
    )

    assert isinstance(evidence, dict), (
        "quota evidence must carry machine-readable sufficiency separately "
        "from its human-readable statement"
    )
    assert isinstance(evidence.get("statement"), str) and evidence["statement"].strip()
    assert evidence.get("sufficiency") == {"daily": "unknown", "spend": "unknown"}

    report = _build_report(
        mod,
        daily_quota_statement=evidence,
        max_cost_usd=10.0,
        max_seconds=900.0,
    )
    assert report["verdict"] == "stop_escalate"


_BRACKET_PR_REF_WORKFLOW = """\
name: bracket-form PR ref violation (test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request['head']['sha'] }}
      - run: ./ci/test.sh
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
"""

_BRACKET_SECRET_WORKFLOW = """\
name: bracket-form secret violation (test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.head_ref }}
      - run: ./ci/test.sh
        env:
          PROVIDER_KEY: ${{ secrets['FAKE_PROVIDER_KEY'] }}
"""

_BRACKET_GITHUB_TOKEN_WORKFLOW = """\
name: bracket-form github token violation (test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.head_ref }}
      - run: gh pr edit --add-label reviewed "$PR_URL"
        env:
          GH_TOKEN: ${{ github['token'] }}
"""

_FORK_REPOSITORY_AND_REF_WORKFLOW = """\
name: explicit fork repository and ref violation (test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          repository: ${{ github.event.pull_request['head']['repo']['full_name'] }}
          ref: ${{ github.event.pull_request['head']['ref'] }}
      - run: ./ci/test.sh
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
"""

_BASE_CHECKOUT_WITH_BRACKET_SECRET = """\
name: base checkout with bracket secret (compliant test-only)
on:
  pull_request_target:
jobs:
  safe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./trusted/base-script.sh
        env:
          PROVIDER_KEY: ${{ secrets['FAKE_PROVIDER_KEY'] }}
"""

_COMMENT_ONLY_SECRET_NEAR_MISS = """\
name: comment-only secret example (compliant test-only)
on:
  pull_request_target:
permissions: {}
jobs:
  safe:
    permissions: {}
    runs-on: ubuntu-latest
    steps:
      # Documentation only; no secret expression is evaluated here:
      # PROVIDER_KEY: ${{ secrets.FAKE_DOCUMENTATION_ONLY }}
      - name: anonymously fetch and execute the public fork PR head
        env:
          GH_TOKEN: ""
          GITHUB_TOKEN: ""
          GIT_ASKPASS: /bin/false
          GIT_CONFIG_NOSYSTEM: "1"
          GIT_TERMINAL_PROMPT: "0"
          HOME: ${{ runner.temp }}/anonymous-git-home
          PR_REPOSITORY: ${{ github.event.pull_request['head']['repo']['full_name'] }}
          PR_SHA: ${{ github.event.pull_request['head']['sha'] }}
        run: |
          mkdir -p "$HOME"
          git init pr-head
          git -C pr-head remote add origin "https://github.com/${PR_REPOSITORY}.git"
          git -C pr-head -c credential.helper= -c http.extraHeader= fetch --no-tags --depth=1 origin "$PR_SHA"
          git -C pr-head checkout --detach FETCH_HEAD
          make -C pr-head test
"""


def _lint_fixture_classifications(mod, tmp_path, fixtures):
    classifications = {}
    for name, content in fixtures.items():
        path = tmp_path / name
        path.write_text(content)
        classifications[name] = bool(list(mod.lint_workflows([path])))
    return classifications


def test_lint_classifies_bracket_forms_and_explicit_fork_checkout_without_false_positives(
    tmp_path,
):
    # spec(W2-M24:AC-5; W2-D8/§6a)
    # guards: dot-notation-only matching for PR refs, secrets, or github.token;
    # also freezes actions/checkout's explicit fork repository+ref form. The
    # two near misses prove hardening does not flag a base checkout or a secret
    # expression that exists only in a YAML comment. The comment fixture's
    # PR-head fetch is anonymous: no checkout action (and therefore no default
    # persisted action token), empty token env vars, no permissions, and Git
    # credential helpers/HTTP auth headers explicitly disabled.
    mod = _spike()
    assert "uses: actions/checkout" not in _COMMENT_ONLY_SECRET_NEAR_MISS
    assert _COMMENT_ONLY_SECRET_NEAR_MISS.count("permissions: {}") == 2
    assert 'GH_TOKEN: ""' in _COMMENT_ONLY_SECRET_NEAR_MISS
    assert 'GITHUB_TOKEN: ""' in _COMMENT_ONLY_SECRET_NEAR_MISS
    assert 'GIT_CONFIG_NOSYSTEM: "1"' in _COMMENT_ONLY_SECRET_NEAR_MISS
    assert "anonymous-git-home" in _COMMENT_ONLY_SECRET_NEAR_MISS
    assert "credential.helper=" in _COMMENT_ONLY_SECRET_NEAR_MISS
    assert "http.extraHeader=" in _COMMENT_ONLY_SECRET_NEAR_MISS
    fixtures = {
        "bracket_pr_ref.yml": _BRACKET_PR_REF_WORKFLOW,
        "bracket_secret.yml": _BRACKET_SECRET_WORKFLOW,
        "bracket_github_token.yml": _BRACKET_GITHUB_TOKEN_WORKFLOW,
        "fork_repository_and_ref.yml": _FORK_REPOSITORY_AND_REF_WORKFLOW,
        "base_checkout_near_miss.yml": _BASE_CHECKOUT_WITH_BRACKET_SECRET,
        "comment_only_secret_near_miss.yml": _COMMENT_ONLY_SECRET_NEAR_MISS,
    }
    actual = _lint_fixture_classifications(mod, tmp_path, fixtures)
    assert actual == {
        "bracket_pr_ref.yml": True,
        "bracket_secret.yml": True,
        "bracket_github_token.yml": True,
        "fork_repository_and_ref.yml": True,
        "base_checkout_near_miss.yml": False,
        "comment_only_secret_near_miss.yml": False,
    }


_RUN_FETCH_HEAD_WITH_SECRET = """\
name: shell fetch PR head with secret violation (test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - name: establish trusted base-repository checkout
        uses: actions/checkout@v4
      - name: fetch and execute the fork PR head
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          git fetch origin "pull/${{ github.event.pull_request.number }}/head:refs/remotes/origin/pr-head"
          git checkout --detach refs/remotes/origin/pr-head
          ./ci/test.sh
"""

_RUN_FETCH_HEAD_WITH_TOKEN = """\
name: shell fetch PR head with github token violation (test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - name: establish trusted base-repository checkout
        uses: actions/checkout@v4
      - name: fetch and execute the fork PR head
        env:
          GH_TOKEN: ${{ github['token'] }}
        run: |
          git fetch "https://x-access-token:${GH_TOKEN}@github.com/${{ github.event.pull_request['head']['repo']['full_name'] }}.git" "${{ github.event.pull_request['head']['sha'] }}"
          git checkout --detach FETCH_HEAD
          ./ci/test.sh
"""

_RUN_FETCH_BASE_NEAR_MISS = """\
name: shell fetch trusted base branch (compliant test-only)
on:
  pull_request_target:
jobs:
  safe:
    runs-on: ubuntu-latest
    steps:
      - name: establish trusted base-repository checkout
        uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          git fetch origin main
          git checkout --detach origin/main
          ./trusted/base-script.sh
"""

_RUN_ECHOED_COMMAND_NEAR_MISS = """\
name: echoed shell example only (compliant test-only)
on:
  pull_request_target:
jobs:
  safe:
    runs-on: ubuntu-latest
    steps:
      - name: establish trusted base-repository checkout
        uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          echo 'git fetch origin pull/123/head:pr-head'
          echo 'git checkout pr-head'
          ./trusted/base-script.sh
"""

_RUN_FETCH_HEAD_UNDER_PULL_REQUEST = """\
name: ordinary pull request shell checkout (compliant test-only)
on:
  pull_request:
jobs:
  unprivileged:
    runs-on: ubuntu-latest
    steps:
      - name: establish event-appropriate checkout and git repository
        uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          git fetch origin "pull/${{ github.event.pull_request.number }}/head:pr-head"
          git checkout --detach pr-head
          make test
"""


def test_lint_classifies_run_body_pr_head_fetch_checkout_with_secret_or_token(
    tmp_path,
):
    # spec(W2-M24:AC-5; W2-D8/§6a)
    # guards: restricting the checkout leg to actions/checkout. Direct shell
    # fetch+checkout is the same pwn-request primitive. Base-branch commands,
    # echoed examples, and the unprivileged pull_request event remain valid
    # near misses and must not be swept up by a keyword-only detector.
    mod = _spike()
    fixtures = {
        "run_fetch_head_secret.yml": _RUN_FETCH_HEAD_WITH_SECRET,
        "run_fetch_head_token.yml": _RUN_FETCH_HEAD_WITH_TOKEN,
        "run_fetch_base_near_miss.yml": _RUN_FETCH_BASE_NEAR_MISS,
        "run_echoed_commands_near_miss.yml": _RUN_ECHOED_COMMAND_NEAR_MISS,
        "run_pull_request_near_miss.yml": _RUN_FETCH_HEAD_UNDER_PULL_REQUEST,
    }
    actual = _lint_fixture_classifications(mod, tmp_path, fixtures)
    assert actual == {
        "run_fetch_head_secret.yml": True,
        "run_fetch_head_token.yml": True,
        "run_fetch_base_near_miss.yml": False,
        "run_echoed_commands_near_miss.yml": False,
        "run_pull_request_near_miss.yml": False,
    }


class _FakeUsage:
    input_tokens = 10
    output_tokens = 2


class _FakeTextBlock:
    type = "text"
    text = "synthetic provider response"


class _FakeMessage:
    usage = _FakeUsage()
    content = [_FakeTextBlock()]


class _FakeRawResponse:
    headers = {}
    retries_taken = 0

    @staticmethod
    def parse():
        return _FakeMessage()


class _RecordingRawMessages:
    def __init__(self, calls):
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        return _FakeRawResponse()


class _RecordingMessages:
    def __init__(self, calls):
        self.with_raw_response = _RecordingRawMessages(calls)


class _RecordingAnthropicClient:
    def __init__(self):
        self.calls = []
        self.messages = _RecordingMessages(self.calls)


def test_live_unit_explicitly_pins_temperature_on_vlm_answer_and_judge_calls():
    # supplemental(W2-D8/§7): live-call temperature configuration guard
    # guards: relying on provider defaults. W2-D8 binds judge temperature to
    # exactly zero and requires every agent call (each VLM page and answer) to
    # be explicitly temperature-pinned and stable. VLM and answer temperatures
    # may differ; corresponding calls may not drift between otherwise identical
    # runs. This offline fake is not evidence for AC-7's live measurement.
    mod = _spike()

    def record_one_run():
        client = _RecordingAnthropicClient()
        mod._run_unit(
            client=client,
            model="synthetic-model-2026-07-14",
            pricing=(3.0, 15.0),
            pages=2,
        )
        return client.calls

    runs = [record_one_run(), record_one_run()]
    for run_number, calls in enumerate(runs, start=1):
        assert len(calls) == 4  # two VLM pages + answer + judge
        missing = [
            index for index, call in enumerate(calls) if "temperature" not in call
        ]
        assert missing == [], (
            f"provider run {run_number} missing explicit temperature at indexes {missing}"
        )
        for call in calls[:-1]:
            assert isinstance(call["temperature"], (int, float))
        assert calls[-1]["temperature"] == 0

    first_agent_temperatures = [call["temperature"] for call in runs[0][:-1]]
    second_agent_temperatures = [call["temperature"] for call in runs[1][:-1]]
    assert first_agent_temperatures == second_agent_temperatures, (
        "corresponding VLM-page and answer temperatures must be stable across runs; "
        f"got {first_agent_temperatures!r} then {second_agent_temperatures!r}"
    )


# ===========================================================================
# Post-GREEN independent security freeze — additional PRT pwn-request
# equivalents and job-local correlation. Appended after the approved
# 65cd239 freeze; every byte above this marker remains immutable.
# ===========================================================================


@pytest.mark.parametrize(
    "quota_evidence",
    [
        pytest.param(
            {"sufficiency": {"daily": "sufficient", "spend": "sufficient"}},
            id="missing_statement",
        ),
        pytest.param(
            {
                "statement": "",
                "sufficiency": {"daily": "sufficient", "spend": "sufficient"},
            },
            id="blank_statement",
        ),
        pytest.param(
            {
                "statement": "   ",
                "sufficiency": {"daily": "sufficient", "spend": "sufficient"},
            },
            id="whitespace_statement",
        ),
        pytest.param(
            {
                "statement": None,
                "sufficiency": {"daily": "sufficient", "spend": "sufficient"},
            },
            id="null_statement",
        ),
        pytest.param(
            {
                "statement": 7,
                "sufficiency": {"daily": "sufficient", "spend": "sufficient"},
            },
            id="non_string_statement",
        ),
    ],
)
def test_structured_quota_requires_a_nonempty_string_statement(quota_evidence):
    # spec(W2-M24:AC-3; W2-D8/§7 quota evidence completeness)
    # guards: certifying exact daily/spend statuses while the required
    # human-readable quota statement is absent, blank, or not renderable as
    # text. AC-3 requires both the statement and a fail-closed verdict.
    mod = _spike()
    report = _build_report(
        mod,
        daily_quota_statement=quota_evidence,
        max_cost_usd=10.0,
        max_seconds=900.0,
    )

    assert report["quota_fit"] is False
    assert report["verdict"] == "stop_escalate"


_DEFAULT_PERSISTED_TOKEN_PR_HEAD_CHECKOUT = """\
name: default checkout token against PR head (violating test-only)
on:
  pull_request_target:
permissions:
  contents: write
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - name: checkout attacker PR with the action defaults
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - run: ./ci/test.sh
"""

_CD_AND_GIT_FETCH_WORKFLOW = """\
name: cd then git fetch PR head (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          path: trusted
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          cd trusted && git fetch origin "pull/${{ github.event.pull_request.number }}/head:refs/remotes/origin/review-candidate"
          git checkout --detach refs/remotes/origin/review-candidate
          ./ci/test.sh
"""

_CHAINED_FETCH_AND_CHECKOUT_WORKFLOW = """\
name: chained fetch and checkout (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          git fetch origin "pull/${{ github.event.pull_request.number }}/head:refs/remotes/origin/review-candidate" && git checkout --detach refs/remotes/origin/review-candidate
          ./ci/test.sh
"""

_ENV_PREFIXED_GIT_WORKFLOW = """\
name: env-prefixed git commands (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          env TRACE_FETCH=1 git fetch origin "pull/${{ github.event.pull_request.number }}/head:refs/remotes/origin/review-candidate"
          env TRACE_CHECKOUT=1 git checkout --detach refs/remotes/origin/review-candidate
          ./ci/test.sh
"""

_RESET_FETCH_HEAD_WORKFLOW = """\
name: reset to fetched PR head (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          git fetch origin "pull/${{ github.event.pull_request.number }}/head"
          git reset --hard FETCH_HEAD
          ./ci/test.sh
"""

_ALIAS_DESTINATION_WORKFLOW = """\
name: arbitrary fetch destination alias (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: |
          git fetch origin "pull/${{ github.event.pull_request.number }}/head:refs/remotes/origin/security-review"
          __ALIAS_SINK__
          ./ci/test.sh
"""

_MIXED_CASE_CHECKOUT_WORKFLOW = """\
name: mixed-case checkout action (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: Actions/Checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - run: ./ci/test.sh
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
"""

_EXPLICIT_FORK_REPOSITORY_WORKFLOW = """\
name: explicit attacker fork repository (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          repository: ${{ github.event.pull_request.head.repo.full_name }}
__OPTIONAL_LITERAL_REF__
      - run: ./ci/test.sh
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
"""

_GH_PR_CHECKOUT_WORKFLOW = """\
name: gh CLI PR checkout (violating test-only)
on:
  pull_request_target:
jobs:
  unsafe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: checkout and execute the attacker PR
        env:
          GH_TOKEN: ${{ github.token }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr checkout "$PR_NUMBER" --force
          ./ci/test.sh
"""


_POST_GREEN_PWN_REQUEST_FIXTURES = [
    pytest.param(
        "default_persisted_token.yml",
        _DEFAULT_PERSISTED_TOKEN_PR_HEAD_CHECKOUT,
        id="checkout_default_persisted_token",
    ),
    pytest.param(
        "cd_then_fetch.yml",
        _CD_AND_GIT_FETCH_WORKFLOW,
        id="cd_and_git_fetch",
    ),
    pytest.param(
        "single_line_fetch_checkout.yml",
        _CHAINED_FETCH_AND_CHECKOUT_WORKFLOW,
        id="single_line_fetch_and_checkout",
    ),
    pytest.param(
        "env_prefixed_git.yml",
        _ENV_PREFIXED_GIT_WORKFLOW,
        id="env_name_value_git",
    ),
    pytest.param(
        "reset_fetch_head.yml",
        _RESET_FETCH_HEAD_WORKFLOW,
        id="git_reset_fetch_head",
    ),
    pytest.param(
        "alias_then_checkout.yml",
        _ALIAS_DESTINATION_WORKFLOW.replace(
            "__ALIAS_SINK__",
            "git checkout --detach refs/remotes/origin/security-review",
        ),
        id="arbitrary_alias_then_checkout",
    ),
    pytest.param(
        "alias_then_reset.yml",
        _ALIAS_DESTINATION_WORKFLOW.replace(
            "__ALIAS_SINK__",
            "git reset --hard refs/remotes/origin/security-review",
        ),
        id="arbitrary_alias_then_reset",
    ),
    pytest.param(
        "mixed_case_action.yml",
        _MIXED_CASE_CHECKOUT_WORKFLOW,
        id="mixed_case_actions_checkout",
    ),
    pytest.param(
        "fork_default_ref.yml",
        _EXPLICIT_FORK_REPOSITORY_WORKFLOW.replace("__OPTIONAL_LITERAL_REF__\n", ""),
        id="explicit_fork_default_ref",
    ),
    pytest.param(
        "fork_literal_ref.yml",
        _EXPLICIT_FORK_REPOSITORY_WORKFLOW.replace(
            "__OPTIONAL_LITERAL_REF__",
            "          ref: attacker-controlled-branch",
        ),
        id="explicit_fork_literal_ref",
    ),
    pytest.param(
        "gh_pr_checkout.yml",
        _GH_PR_CHECKOUT_WORKFLOW,
        id="gh_pr_checkout",
    ),
]


@pytest.mark.parametrize("filename,workflow", _POST_GREEN_PWN_REQUEST_FIXTURES)
def test_lint_rejects_post_green_prt_pwn_request_equivalents(
    tmp_path, filename, workflow
):
    # spec(W2-M24:AC-5; W2-D8/§6a post-GREEN security freeze)
    # guards: parser-specific gaps must not bypass the policy's semantic
    # conjunction: PRT + attacker-controlled code execution + secret/token
    # access. Fixtures use only executable checkout/Git/GH CLI forms.
    mod = _spike()
    path = tmp_path / filename
    path.write_text(workflow)
    if filename == "default_persisted_token.yml":
        # Premise for finding (1): checkout receives/persists github.token via
        # action defaults; the workflow contains no explicit token expression
        # that a text-only secret detector could key on.
        assert "${{ github.token }}" not in workflow
        assert "secrets." not in workflow
        assert "token:" not in workflow
        assert "persist-credentials:" not in workflow

    findings = list(mod.lint_workflows([path]))
    assert findings, f"expected a PRT pwn-request finding for {filename}"
    assert filename in str(findings)


_JOB_LOCAL_CORRELATION_NEAR_MISS = """\
name: isolated anonymous PR job and trusted secret job (compliant test-only)
on:
  pull_request_target:
permissions: {}
jobs:
  anonymous_pr_head:
    permissions: {}
    runs-on: ubuntu-latest
    steps:
      - name: fetch and execute public fork code without credentials
        env:
          GH_TOKEN: ""
          GITHUB_TOKEN: ""
          GIT_ASKPASS: /bin/false
          GIT_CONFIG_NOSYSTEM: "1"
          GIT_TERMINAL_PROMPT: "0"
          HOME: ${{ runner.temp }}/anonymous-pr-home
          PR_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}
        run: |
          mkdir -p "$HOME"
          git init pr-head
          git -C pr-head remote add origin "https://github.com/${PR_REPOSITORY}.git"
          git -C pr-head -c credential.helper= -c http.extraHeader= fetch --no-tags --depth=1 origin "${{ github.event.pull_request.head.sha }}"
          git -C pr-head checkout --detach FETCH_HEAD
          make -C pr-head test
  trusted_secret_only:
    permissions: {}
    runs-on: ubuntu-latest
    steps:
      - name: trusted base-workflow operation with no checkout or artifacts
        env:
          PROVIDER_KEY: ${{ secrets.FAKE_PROVIDER_KEY }}
        run: test -n "$PROVIDER_KEY"
"""


def test_lint_preserves_post_green_security_near_misses(tmp_path):
    # spec(W2-M24:AC-5; W2-D8/§6a false-positive controls)
    # guards: hardening must still ignore comments/echoed commands, trusted
    # base refs, ordinary pull_request jobs, and genuinely anonymous PR-head
    # execution with no credential path.
    mod = _spike()
    fixtures = {
        "trusted_base_action.yml": _BASE_CHECKOUT_WITH_BRACKET_SECRET,
        "comment_anonymous_pr_head.yml": _COMMENT_ONLY_SECRET_NEAR_MISS,
        "echoed_commands.yml": _RUN_ECHOED_COMMAND_NEAR_MISS,
        "trusted_base_ref.yml": _RUN_FETCH_BASE_NEAR_MISS,
        "ordinary_pull_request.yml": _RUN_FETCH_HEAD_UNDER_PULL_REQUEST,
    }
    assert _lint_fixture_classifications(mod, tmp_path, fixtures) == {
        name: False for name in fixtures
    }


def test_lint_correlates_pr_head_execution_and_secret_access_within_one_job(
    tmp_path,
):
    # spec(W2-M24:AC-5; W2-D8/§6a job-local correlation)
    # guards: workflow-global OR logic combining untrusted execution in one
    # credential-free job with a secret used only by a separate isolated job.
    # No needs/artifact/output path connects the jobs, so the forbidden
    # conjunction does not exist in either job.
    mod = _spike()
    assert "uses: actions/checkout" not in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert _JOB_LOCAL_CORRELATION_NEAR_MISS.count("permissions: {}") == 3
    assert 'GH_TOKEN: ""' in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert 'GITHUB_TOKEN: ""' in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert "credential.helper=" in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert "http.extraHeader=" in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert "needs:" not in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert "actions/upload-artifact" not in _JOB_LOCAL_CORRELATION_NEAR_MISS
    assert "actions/download-artifact" not in _JOB_LOCAL_CORRELATION_NEAR_MISS
    path = tmp_path / "isolated_jobs.yml"
    path.write_text(_JOB_LOCAL_CORRELATION_NEAR_MISS)

    assert list(mod.lint_workflows([path])) == []
