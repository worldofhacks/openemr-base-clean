# Clinical Co-Pilot Bruno collection

This is the runnable F5 API collection for the deployed, read-only Clinical Co-Pilot
(§7, G4, D14). It checks process liveness, dependency-aware readiness, and the
authenticated UC1 `/chat` serving flow against synthetic Synthea data.

The public API does not expose individual FHIR tools. The authenticated `/chat` request
is the externally runnable sample tool flow: it performs the six read-only FHIR calls,
builds the EvidencePacket, and returns the verify-then-flush serving envelope.

## Fresh-clone run

Prerequisites: Python 3.12+, Node.js, Docker, and the synthetic-demo OpenEMR tester
credentials from `DEPLOYMENT.md` §8. The registered SMART client must be enabled per
D14. Never use real PHI or real clinical credentials.

From the repository root:

```bash
docker compose -f docker/development-easy/docker-compose.yml up --detach --wait selenium
cd agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python bruno/dev_mint.py
cd bruno
npx --yes @usebruno/cli@3.5.1 run --env Runtime --bail
```

`dev_mint.py` prompts without echo for the synthetic-demo OpenEMR password, drives the
browser-only SMART launch, and writes a short-lived opaque agent session to the
gitignored `environments/Runtime.bru`. The OAuth bearer token never leaves the agent.
Before entering credentials it verifies the browser reached the expected HTTPS OpenEMR
origin, and it accepts plaintext WebDriver transport only on loopback. At the final
callback it validates and captures `/app?sid=...` while blocking the page's automatic
`/chat` network request; Bruno remains the only chat caller. See [mint-token.md](mint-token.md)
for options and troubleshooting.

The collection runs sequentially:

1. `GET /health` must return the process-only 200 contract.
2. `GET /ready` validates the real dependency report. A 503 is a valid, tested result
   only when a hard dependency is reported down; otherwise the request must return 200.
3. `POST /chat` uses the minted, patient-pinned session and validates the current JSON
   serving envelope, a non-empty brief, and the correlation ID. The model call can take
   up to three minutes.

To exercise only the unauthenticated checks:

```bash
npx --yes @usebruno/cli@3.5.1 run health.bru ready.bru --env Deployed --bail
```

To target another deployment, set `AGENT_BASE_URL` before running the mint helper. Delete
`environments/Runtime.bru` when finished; an agent restart or session expiry also requires
a fresh launch.

The helper's isolated unit checks live with the collection (the agent's canonical pytest
configuration intentionally discovers only `agent/tests/`). From the repository root:

```bash
cd agent
python -m unittest discover -s bruno/tests -v
```

## Sanitized deployed validation

The authenticated end-to-end collection run completed against the synthetic-data
deployment on 2026-07-12: `/health` returned 200, `/ready` returned 200, and `/chat`
returned 200 in 42,373 ms. Bruno reported 3/3 requests and 10/10 tests passing in
46,918 ms total. Credentials and the opaque session ID were not retained; the generated
`environments/Runtime.bru` was deleted after the run.

## Contract note

The collection follows the deployed E9 contract: browser `/launch` → `/callback` →
captured `/app?sid=...`, then a JSON `POST /chat`. `ARCHITECTURE.md` §5a still describes
the planned SSE `/chat` and `POST /sessions` surface. Reconciling that binding contract
is a planning-pass decision, not part of F5; this collection does not alter or bypass the
serving routes.
