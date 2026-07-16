# F6 k6 load profiles

The legacy Week 1 scripts are bounded probes for the deployed read-only API. The Week 2
profile below additionally uploads committed synthetic documents to an already synthetic
deployment; it must never target a real patient or real clinical document.

The commands below use the pinned `grafana/k6:2.1.0` image. k6's
[`per-vu-iterations`](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/per-vu-iterations/)
executor makes the request count exact: total iterations are `vus * iterations`.

## Public `/health` and `/ready`

`public_baseline.js` accepts only 10 or 50 VUs. Every VU performs exactly one `/health` and
one `/ready` request, so the runs issue exactly 20 and 100 requests respectively.

```bash
docker run --rm \
  -e BASELINE_VUS=10 \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/public_baseline.js

docker run --rm \
  -e BASELINE_VUS=50 \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/public_baseline.js
```

The default target is the deployed agent. Override it only with another HTTPS endpoint:
`-e BASE_URL=https://agent.example.org`.

By default a non-200 `/ready` fails the `ready_errors` threshold. For a diagnostic capture
of a known-unready deployment, pass `-e ALLOW_NOT_READY=true`. That mode accepts a semantic
503 response for checks but still records `ready_errors=100%` and the built-in HTTP failure
rate; it never relabels the deployment healthy.

## Authenticated `/chat`: one request, hard client bound

`chat_capped.js` cannot run with a default or missing session. It aborts before network I/O
unless all four values below are supplied. The scenario is fixed at one VU and one iteration;
there is no environment variable that can raise the request count. A setup-time check also
aborts if CLI or runtime options replace the single local scenario. Do not run this profile
through a distributed/cloud executor: its dollar boundary applies to one load generator.

```bash
export CHAT_BASE_URL=https://agent-production-9f62.up.railway.app
read -rsp "SMART session id: " CHAT_SESSION_ID && export CHAT_SESSION_ID
export CHAT_SPEND_CAP_USD=2.62
export CHAT_PROFILE_ACK=sonnet-4.6-200k-8192-single-call-retries2

docker run --rm \
  -e CHAT_BASE_URL \
  -e CHAT_SESSION_ID \
  -e CHAT_SPEND_CAP_USD \
  -e CHAT_PROFILE_ACK \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/chat_capped.js

unset CHAT_SESSION_ID CHAT_SPEND_CAP_USD CHAT_PROFILE_ACK
```

Never place the session id directly in the command line or commit it. The response body is
validated in memory and is not printed or written by the script.

### Spend-bound proof

The acknowledged profile is the current deployed contract: Sonnet 4.6, 200K-class context,
8,192 maximum output tokens, one forced `submit_claims` completion, and two SDK retries. The
conservative bound deliberately prices a full 200,000 input tokens at the 1.25x cache-write
rate, adds the full output ceiling, and allows three provider attempts:

```text
per attempt = 200,000 * $3/M * 1.25 + 8,192 * $15/M = $0.87288
three attempts = $2.61864 -> required cap rounds up to $2.62
```

The supplied cap must be between $2.62 and the hard script maximum of $3.00. A lower cap
cannot safely authorize even one request under this conservative model; a higher cap would
weaken the intentionally low test boundary. If the deployed model, context, max output,
retry count, or single-call serving contract changes, the profile acknowledgement becomes
false and this script must be reviewed before use. This is a bounded test authorization, not
a substitute for provider billing reconciliation.

## Guardrail tests

The guard tests exercise nine abort-before-network paths, including a fully valid
configuration in `CHAT_VALIDATE_ONLY` mode, and use synthetic placeholder data:

```bash
agent/load/k6/tests/guardrails.sh
```

## Week 2 1/10/50-VU flow ladder

`w2_profiles.js` runs three disjoint `per-vu-iterations` scenarios: 1, then 10,
then 50 concurrent users. Supply a mode with `PROFILE=retrieval|ingestion|extraction|full_graph|week1`.
The ingestion mode measures a new-upload 202 only; extraction measures from upload through
the terminal completed job. Retrieval sends the required session-pin header. Week 1 checks
`/health`, `/ready`, and `/chat`; full graph requires an explicit acknowledgement that the
target deployment has the Week 2 graph enabled.

The application intentionally limits document work to one concurrent upload per session.
The profile therefore requires 61 distinct, synthetic SMART-pinned contexts: one for the
1-VU stage, ten new contexts for the 10-VU stage, and fifty new contexts for the 50-VU
stage. Reusing one session or patient would measure quota rejection and permanent document
deduplication instead of the requested flow. The server's global four-upload admission cap
is exercised with a bounded 503 retry; terminal capacity failure remains an error rather
than being relabeled as latency. Store these fields in a private, untracked JSON
file with this shape; never commit or print the populated file:

```json
[
  {"session_id": "opaque synthetic session", "patient_id": "synthetic patient"}
]
```

For document modes, mount a committed synthetic PDF and the private context file read-only.
Every mode requires `SYNTHETIC_ONLY_ACK=synthetic-sessions-and-documents`. Provider-bearing
modes also require `ALLOW_PROVIDER_SPEND=true`; even ingestion triggers asynchronous worker
extraction after the upload response. Example container arguments (paths are illustrative):

```bash
docker run --rm \
  -e PROFILE=extraction \
  -e AGENT_BASE_URL=https://agent.example.org \
  -e SYNTHETIC_CONTEXTS_FILE=/private/contexts.json \
  -e SYNTHETIC_FIXTURE=/fixtures/lab-clean-glucose.pdf \
  -e SYNTHETIC_ONLY_ACK=synthetic-sessions-and-documents \
  -e ALLOW_PROVIDER_SPEND=true \
  -v /private/w2-load:/private:ro \
  -v "$PWD/agent/evals/fixtures/golden:/fixtures:ro" \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/w2_profiles.js
```

Retain only aggregate k6 output and separately sampled deployment CPU/memory, version,
deployment ID, exact SHA, token, and cost totals. Do not retain response bodies, context
files, request URLs containing opaque identifiers, prompts, or document contents.
