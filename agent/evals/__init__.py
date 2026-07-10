"""Eval framework (ARCHITECTURE.md §8). Production-grade: boundary / invariant / regression /
adversarial cases — happy-path-only fails. The required synthetic fixtures (deceased,
empty-allergy, LLM-failure, FHIR-failure) exercise safety paths the demo data cannot (D7 rev
2026-07-07, D12, F-D.5, D13, F3). Runs offline (mocks, no live services) so it is the CI
eval deploy-gate: evals must be green before E9 deploys."""
