# Build posture: production-grade
E6 is the safety-critical verification layer (§5, D7). All quality gates in scope:
frozen tests reviewed before freeze, implementation cannot edit tests, independent
reviewer verifies against DoD, orchestrator re-runs gates itself (trust nothing),
no merge to main without owner approval. Owner (user) explicitly requested real
separation of powers.
