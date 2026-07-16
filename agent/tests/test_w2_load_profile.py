"""Static guardrails for the bounded synthetic-only Week 2 k6 profile."""

from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load/k6/w2_profiles.js"


def test_w2_profile_contains_exact_1_10_50_vu_ladder_and_slo_bounds():
    text = SCRIPT.read_text(encoding="utf-8")
    for value in ("vus: 1", "vus: 10", "vus: 50"):
        assert value in text
    assert '"p(95)<2000"' in text
    assert '"p(95)<30000"' in text


def test_w2_profile_requires_explicit_spend_and_never_logs_payloads():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "ALLOW_PROVIDER_SPEND" in text
    assert '"ingestion", "extraction", "full_graph", "week1"' in text
    assert "SYNTHETIC_ONLY_ACK" in text
    assert "GRAPH_ENABLED_ACK" in text
    assert "console.log" not in text
    assert "response.body" not in text
    assert "discardResponseBodies: false" in text


def test_w2_profile_uses_disjoint_synthetic_contexts_and_real_route_contracts():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "const contextOffsets = { vu1: 0, vu10: 1, vu50: 11 }" in text
    assert "const requiredContextCount = 61" in text
    assert "SYNTHETIC_CONTEXTS_FILE" in text
    assert "synthetic sessions must not be reused" in text
    assert "document patients must not be reused" in text
    assert '"X-Copilot-Session-Id": context.session_id' in text
    assert "response.status === 202" in text
    assert "attempt < 30" in text
    assert "response.status !== 503" in text
    assert 'body.state === "complete"' in text
    assert 'profile === "ingestion"' in text
    assert 'profile === "extraction"' in text


def test_w2_profile_records_end_to_end_flow_not_only_upload_latency():
    text = SCRIPT.read_text(encoding="utf-8")

    complete = text.index('if (body.state === "complete")')
    successful_record = text.index("record(true, started);", complete)
    poll = text.index("document_status")
    assert poll < complete < successful_record
    assert 'new Rate("w2_profile_degraded")' in text
    assert 'tags: { name: "document_status" }' in text
