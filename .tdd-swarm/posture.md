# Build posture: production-grade
E6 is the safety-critical verification layer (§5, D7). All quality gates in scope:
frozen tests reviewed before freeze, implementation cannot edit tests, independent
reviewer verifies against DoD, orchestrator re-runs gates itself (trust nothing),
no merge to main without owner approval. Owner (user) explicitly requested real
separation of powers.

# W2 Wave 0 addendum (2026-07-14): production-grade carried forward. Formatting/
# linting/typecheck/coverage gates are SKIPPED-with-reason (no such tooling exists
# in agent/ — see gates.md); all other gates active. Wave 0 spikes are themselves
# the performance-measurement gates (M1 RSS ceiling, M24 timing/cost budget).
