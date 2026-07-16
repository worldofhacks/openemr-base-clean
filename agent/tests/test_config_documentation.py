"""The environment template stays synchronized with actual configuration inputs."""

from __future__ import annotations

from pathlib import Path
import re

from app.config import Settings


TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"
DIRECT_RUNTIME_KEYS = {
    "W2_GRAPH_ENABLED",
    "RERANKER",
    "COHERE_API_KEY",
    "EVIDENCE_CORPUS_DIR",
}
RETIRED_KEYS = {
    "OPENEMR_LEGACY_PATIENT_UUID",
    "OPENEMR_LEGACY_PATIENT_ID",
    "OPENEMR_LEGACY_ENCOUNTER_UUID",
    "OPENEMR_LEGACY_ENCOUNTER_ID",
}


def _documented_keys() -> set[str]:
    pattern = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=")
    return {
        match.group(1)
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if (match := pattern.match(line))
    }


def test_env_example_documents_every_setting_and_direct_runtime_switch():
    settings = {name.upper() for name in Settings.model_fields}
    assert _documented_keys() == settings | DIRECT_RUNTIME_KEYS


def test_env_example_contains_no_retired_singleton_patient_route_variables():
    assert _documented_keys().isdisjoint(RETIRED_KEYS)


def test_env_example_uses_no_live_service_urls_or_secret_shaped_placeholders():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "railway.app" not in text
    assert "sk-ant-" not in text
    assert "postgresql://" not in text
