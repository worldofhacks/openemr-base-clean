"""Synthetic eval fixtures for safety paths the demo data cannot exercise (DECISIONS D7 rev
2026-07-07): all 25 Synthea patients are alive and have allergy records, so the deceased
hard-stop (D12) and the empty-allergy phrasing (F-D.5) — plus LLM-failure (D13) and
FHIR-failure (F3) — must be driven by mocked fixtures, not live data."""
