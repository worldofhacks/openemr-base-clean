"""Observability (ARCHITECTURE.md §7, D5-rev). The Langfuse trace is the HIPAA
system-of-record for accountability because OpenEMR's api_log cannot attribute a request
to the Co-Pilot OAuth client or its exercised scopes (F-C.1). Traces are PHI-minimized
(hashes, not identifiers, D5) and the export is a soft dependency (§6)."""
