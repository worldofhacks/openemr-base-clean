# Clinical Co-Pilot Bruno collection

This is the runnable Week 1/Week 2 grader collection for the Clinical Co-Pilot. It uses
synthetic fixtures only and checks liveness, readiness, upload/extraction/readback,
retrieval, CitationV2 chat, and permanent duplicate handling.

The public API does not expose individual FHIR tools. The authenticated `/chat` request
is the externally runnable sample tool flow: it performs the six read-only FHIR calls,
builds the EvidencePacket, and returns the verify-then-flush serving envelope.

## Fresh-clone Week 2 run (no Selenium)

Prerequisites: Python 3.12+, Node.js, an ordinary web browser, and the synthetic-demo
OpenEMR tester credentials from `DEPLOYMENT.md` §8. The registered SMART client must be
enabled per D14. Never use real PHI or real clinical credentials.

From the repository root:

```bash
cd agent
python3 bruno/dev_mint.py --manual --flow week2
cd bruno
npx --yes @usebruno/cli@3.5.2 run --env Runtime --bail
```

Manual mode opens the real `/week2/launch` flow in the default browser. Sign in with the
synthetic tester, select the synthetic patient, approve the client, and copy the final
`/week2?sid=...` address back to the helper's **non-echoing** prompt. The helper validates
the exact agent origin/path/query and writes only the opaque session id to the gitignored
`environments/Runtime.bru` with mode `0600`; the OAuth bearer token never leaves the
agent. No Selenium grid, browser driver, or credential argument is involved. See
[mint-token.md](mint-token.md) for the retained automated compatibility path and
troubleshooting.

The ten grader flows run sequentially (the intake idempotency flow uses two requests):

1. `GET /health` must return the process/SHA 200 contract.
2. `GET /ready` validates the real dependency report. A 503 is a valid, tested result
   only when a hard dependency is reported down; otherwise the request must return 200.
3. A synthetic lab is uploaded, then status is polled once per second with a hard cap of
   30 attempts. The run fails closed if the job fails or does not complete in that bound.
4. The extraction report, PNG page preview, and fresh source/artifact digest readback are
   checked for the same patient-pinned document.
5. PHI-free guideline evidence and session-authenticated cited chat validate the retrieval
   and CitationV2-only serving envelopes.
6. The same synthetic intake bytes are uploaded twice; the second request must return
   the same permanent document id with HTTP 200.

Use the Week 1 default only for the read-only checks and chat:

```bash
python bruno/dev_mint.py --flow week1
```

Guideline retrieval is PHI-free and has a shared bounded anonymous budget, so a fresh
grader can run it without minting a patient session:

```bash
cd agent/bruno
npx --yes @usebruno/cli@3.5.2 run evidence-search.bru --env Deployed --bail
```

To exercise all unauthenticated checks:

```bash
npx --yes @usebruno/cli@3.5.2 run health.bru ready.bru evidence-search.bru --env Deployed --bail
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

No bearer token, patient identifier, document content, prompt, or transcript is written
to the collection environment or printed by the mint helper. Delete
`environments/Runtime.bru` after the run.
