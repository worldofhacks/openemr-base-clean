"""Verification + rendering (ARCHITECTURE.md §5, the load-bearing trust layer).

E5 lands the deterministic D13 templater path (`templater.render_packet_fallback`).
E6 extends this package with the typed-claims verifier and the verified-claims
re-render (`render_from_verified_claims`) plus the concrete §5 rules.
"""
