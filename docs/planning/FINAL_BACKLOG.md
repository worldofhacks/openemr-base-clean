# Clinical Co-Pilot — Audit-First Final Backlog

> **Snapshot:** `origin/main` at `10ac890` (2026-07-11).
> **Source:** still-open findings CXR-04 through CXR-13 from `docs/reviews/CODEX_GAP_REVIEW.md` in draft PR #3.
> **Purpose:** prioritized input to the Final implementation plan; this document changes no binding architecture or implementation.

## Prioritization method

The release tag is the first ordering dimension:

- **Final-blocker:** must close before claiming the production-grade Final milestone. “Close” can be a tested implementation or, where explicitly stated, a binding `/arch-finalize` decision that accurately narrows the contract.
- **Hardening:** important and expected in Final, but current deterministic safeguards bound the immediate clinical risk. If deferred, retain the acceptance test and name the residual risk.
- **Acceptable-with-documented-note:** current behavior is safe enough for the defined demo posture; the mismatch must be made explicit in the binding docs and tracker before Final is called complete.

Within each tag, rank is audit consequence first (clinical safety, authorization, HIPAA accountability), then current severity, then lower relative effort. Effort is intentionally coarse:

- **S:** localized change and focused regression coverage.
- **M:** crosses two or more seams or requires live evidence.
- **L:** storage/topology or clinical-selection policy with migration/load implications.

This is a severity-by-effort sequence, not a promise of elapsed time. A verified high-severity audit control remains ahead of an easier polish item.

## Ranked backlog

| Rank | ID | Release tag | Severity | Effort | Audit trace | Final disposition |
|---:|---|---|---|---|---|---|
| 1 | CXR-05 | **Final-blocker** | High | M | **F-C.1**, **F-C.2**; F-A.5/F-P.6, F-P.5 | Trace every real FHIR read inside the authoritative accountability record |
| 2 | CXR-06 | **Final-blocker** | High | S | F-S.1, F-C.5; F-S.6 context | Enforce granted-scope coverage before session/token persistence |
| 3 | CXR-04 | **Final-blocker** (decision) | High | S docs / L scope redesign | F-S.1, F-S.2, F-C.5; F-S.6 | Reconcile actual `user/*` breadth with the binding patient-containment claim |
| 4 | CXR-09 | **Final-blocker** | Medium | M | F-S.5; F-C.5 lifecycle rationale | Bind sessions to actual token expiry and type 401 as reauthentication |
| 5 | CXR-11 | **Final-blocker** | Medium | L | F-D.6, **F-P.3**, F-P.5, F-C.5 | Prevent silent omissions with bounded pagination and deterministic selection |
| 6 | CXR-07 | **Final-blocker** | High | L | F-S.2 | Make the serving store, token lifecycle, and readiness claim describe one real system |
| 7 | CXR-12 | **Final-blocker** | Medium | M | **F-D.1**, **F-D.4**, F-D.2/F-D.5/F-D.6 | Prevent generic D13 rendering from diverging as verifier rules expand |
| 8 | CXR-10 | **Hardening** | High | S | F-D.6 context; §6/F3 contract | Contain mapper errors to one named partial failure |
| 9 | CXR-13 | **Hardening** | Medium | M | **F-C.1**, **F-C.2**, F-P.5 | Move mandatory observability export off the response latency path without losing accountability |
| 10 | CXR-08 | **Acceptable-with-documented-note** | High contract mismatch; bounded runtime risk | S decision / L SSE | F-D.1/F-D.4/F-D.5 rationale; no direct audit defect | Adopt atomic verified JSON explicitly, or retain SSE as a formal implementation blocker |

## Final blockers

### 1. CXR-05 — Trace coverage begins after the six FHIR reads

- **Why first:** F-C.1 is a verified high-severity §164.312(b) attribution gap in OpenEMR; F-C.2 proves there is no reliable cross-system join. D5 therefore makes the agent trace the authoritative record, but the PHI reads currently occur before that record begins.
- **Current evidence:** `agent/app/service.py:121-125` completes fan-out and packet construction before `AccountabilityContext` and the orchestrator at `agent/app/service.py:126-139`; tracing begins inside the orchestrator at `agent/app/orchestrator/loop.py:272-278`. `agent/app/tools/fhir_client.py:43-62` carries a correlation header but emits no call span.
- **Audit trace:** F-C.1, F-C.2/F-A.5/F-P.6; F-P.5 supplies latency context.
- **Dependencies:** E7.0 live Langfuse; a trace-context seam usable by `FhirClient`; feeds F2 dashboard, F3 alerts, F4 latency, and F7 cost evidence.
- **Acceptance:** the trace exists before the first outbound FHIR call and records one sanitized success/failure/timeout span per call, including correlation ID, client ID, exercised scopes, clinician/patient hashes, resource route, UTC timestamp, latency, and outcome—even when the LLM is never reached. Prove it with unit coverage and one live Langfuse trace.

### 2. CXR-06 — Granted-scope guard is not on the production exchange path

- **Why second:** it is the smallest high-value trust-boundary closure. F-S.1 says the granted scope set is the actual authorization ceiling; F-C.5 keeps minimum-necessary enforcement on the agent.
- **Current evidence:** `agent/app/auth/scopes.py:51-58` implements the guard, but `agent/app/service.py:91-101` exchanges the token and creates/persists the session without calling it.
- **Audit trace:** F-S.1 and F-C.5; F-S.6 documents the actual disabled-by-default `user/*` client posture.
- **Dependencies:** none; complete before other authorization refactors.
- **Acceptance:** invoke the guard immediately after exchange and before storing a token or creating a session. A missing scope returns a sanitized explicit failure, creates no session, persists no token, and is frozen through the real callback/service path.

### 3. CXR-04 — `user/*` grant breadth contradicts patient-scoped binding language

- **Why a blocker:** this is primarily a binding-contract decision, not proof of a current cross-patient exploit. Leaving the trust claim overstated is unacceptable in a hospital-CTO Final defense.
- **Current evidence:** `agent/app/auth/scopes.py:23-35` requests clinician `user/*` reads; `ARCHITECTURE.md:86` still describes patient-scoped/compartment-bound tokens, while `ARCHITECTURE.md:135` lists the real `user/*` set. Current tools remain constrained by the D12 session pin.
- **Audit trace:** F-S.1 (scope/compartment wording), F-S.2 (server patient-access check is a stub), F-C.5 (minimum necessary), and F-S.6 (user-scoped clients require enablement).
- **Recommended closure (S):** route through `/arch-finalize` and explicitly adopt delegated clinician `user/*` scopes plus the agent-enforced patient pin. State that launch context does not transform the grant into a server-enforced patient-scoped token, name the residual bearer-token breadth, and retain cross-patient/session-pin invariants.
- **Alternative (L):** if the owner requires server-enforced patient-compartment scope, redesign and re-prove the SMART grant rather than changing prose only.
- **Acceptance:** one unambiguous authorization model across D2/D9/D12/§4/§5a, with the granted-scope guard from CXR-06 and a documented residual-risk owner.

### 4. CXR-09 — Session lifetime ignores actual token expiry

- **Why a blocker:** an expired grant currently degrades into “missing clinical data,” which is materially different from “reauthentication required.” That can make an authorization failure look like a chart finding.
- **Current evidence:** `agent/app/auth/smart_client.py:91` captures `expires_in`; `agent/app/service.py:98-106` uses only configured lifetime; `agent/app/tools/fhir_client.py:57-58` collapses 401 into a generic error and `agent/app/tools/fhir_tools.py:146-154` turns it into ordinary partial absence.
- **Audit trace:** F-S.5 confirms delegated clinician authorization; F-C.5 supports least-privilege duration. The lifecycle defect itself was not a standalone audit finding.
- **Dependencies:** session lifecycle semantics; coordinate with CXR-07 but it can land independently.
- **Acceptance:** session deadline is the minimum of actual token expiry, configured ceiling, idle timeout, and turn cap. A FHIR 401 becomes a typed reauthentication outcome, invalidates/refuses the session, never becomes six missing-data notices, and tells the clinician to relaunch. Cover shorter/longer/missing `expires_in` plus 401.

### 5. CXR-11 — Pagination, recency, and selection can silently omit records

- **Why a blocker:** a cited brief can still be incomplete without saying so. F-D.6 makes dates/status selection clinically meaningful, while F-P.3 proves blindly exhausting the lab path can be pathologically expensive.
- **Current evidence:** `agent/app/tools/fhir_client.py:43-68` consumes one Bundle; `agent/app/tools/fhir_tools.py:162-188` uses fixed counts without ordering/lookback; `agent/app/tools/contracts.py:119-122` declares an unused lookback. This server-page loss is silent. Later prompt-budget trimming retains first-arrival order but, distinctly, emits a `trimmed` notice at `agent/app/evidence/packet.py:245-268`.
- **Audit trace:** F-D.6, F-P.3, F-P.5, and F-C.5.
- **Dependencies:** explicit clinical selection policy; F6 load evidence for safe bounds; coordinate with F1/F9 rule expansion.
- **Acceptance:** deterministic sort/recency policy; real lookback behavior; bounded pagination follows `next` links up to a tested budget or emits an exact/unknown truncation notice. Newest/selected records cannot be silently displaced by server order. Cover multi-page, out-of-order, stale-date, and large-chart cases without blindly exhausting F-P.3's worst path.

### 6. CXR-07 — In-memory serving state conflicts with Postgres readiness

- **Why a blocker:** F-S.2 establishes the agent-side session pin as the real clinician/patient enforcement. Final cannot claim durable fail-closed storage while serving from memory and probing a different backend.
- **Current evidence:** `agent/app/service.py:58-61` composes in-memory session/token/PKCE state; `agent/app/health.py:71-86` probes Postgres; `agent/app/session/store.py:137-207` contains a Postgres store that is not the runtime composition and does not solve durable encrypted delegated-token storage.
- **Audit trace:** F-S.2. There is no separate audit ID for the readiness/composition mismatch.
- **Dependencies:** Postgres provisioning and driver, encryption/retention decision for delegated tokens, readiness rewrite, multi-replica/load posture.
- **Acceptance:** serving and `/ready` exercise the same real store with an operation stronger than TCP reachability. Pin/turn/expiry state survives process or replica change; token handling is durable and encrypted, or restart explicitly invalidates corresponding DB sessions. Store outage fails closed with 503. In-memory mode is explicit dev-only configuration.

### 7. CXR-12 — Generic D13 rendering bypasses formal verifier execution

- **Why a blocker:** the current deterministic renderer manually honors the important dose/allergy/criticality rules, and all-blocked claims now verify before degrading, so no unsafe leak was demonstrated. F1/F9 necessarily expands verifier-only rules, however, making fallback parity a prerequisite to those required Final tasks rather than deferrable hardening.
- **Current evidence:** generic rendering is selected independently of the normal verified composition path at `agent/app/orchestrator/loop.py:337-355,441-475`; the fallback renderer encodes a separate subset of safety behavior at `agent/app/verify/templater.py:135-159`.
- **Audit trace:** F-D.1, F-D.4, F-D.2, F-D.5, F-D.6.
- **Dependencies:** F1 rule model and verdict schema.
- **Acceptance:** one deterministic policy path governs normal and fallback output; fallback emits verdict/trace data; invariant tests apply every relevant F-D rule—including stale-lab behavior—to generic D13.

## Hardening

### 8. CXR-10 — Mapper exceptions can abort fan-out

- **Risk:** malformed interop input can escape one tool and cancel the partial-answer contract, but this is a localized containment fix and no current production shape was shown to trigger it.
- **Current evidence:** mapping occurs outside the protected block at `agent/app/tools/fhir_tools.py:146-154`; the wrapper catches only timeout at `agent/app/tools/fhir_tools.py:209-214`; `task.result()` can re-raise at `agent/app/tools/fhir_tools.py:219-221`.
- **Audit trace:** no direct F-* root; F-D.6 is contextual evidence that FHIR shapes/status semantics are messy. The direct requirement is §6/F3.
- **Acceptance:** an arbitrary mapper/validation exception becomes one sanitized `FAILED` ToolResult, the other five results survive, no raw payload/PHI appears in the reason, and a malformed-resource regression freezes the behavior.

### 9. CXR-13 — Langfuse export is synchronous

- **Risk:** failures are isolated, but a slow mandatory accountability sink can still delay the clinical response.
- **Current evidence:** trace lifecycle calls synchronously await the configured sink at `agent/app/observability/langfuse.py:75-112`; Langfuse network operations execute inline through `agent/app/observability/langfuse.py:140-180`.
- **Audit trace:** F-C.1/F-C.2 make Langfuse authoritative; F-P.5 supplies latency context.
- **Dependencies:** CXR-05 trace refactor, E7.0 live sink, explicit buffering/drop/backpressure policy.
- **Acceptance:** a slow/hung sink consumes only a defined small response-path budget; use a bounded async queue or timeout with backpressure, a dropped counter and alert, and graceful shutdown flush. A slow-sink test proves latency isolation without silently abandoning compliance visibility.

## Acceptable with a documented note

### 10. CXR-08 — Buffered JSON differs from the binding SSE contract

- **Current narrowing:** latest `main` surfaces citations in `agent/app/routes/chat.py:38-59,89-95` and renders citation chips in `agent/app/routes/ui.py:100-108`. The residual mismatch is atomic buffered JSON versus verified SSE claim blocks and interruption markers.
- **Why acceptance is defensible:** the UI renders only after a complete JSON response, so interruption cannot display a truncated answer as complete. This is a safer atomic boundary, though it gives up incremental perceived latency.
- **Audit trace:** no direct audit defect. F-D.1/F-D.4/F-D.5 explain why verified provenance and deterministic presentation are load-bearing; those protections remain.
- **Recommended closure (S):** route through `/arch-finalize` and explicitly adopt atomic verified JSON as the safety/latency tradeoff. Update §3/§5a/§6 and the E9.2 tracker, and retain a test that aborted/invalid responses render nothing.
- **If SSE remains binding (L):** reclassify this as a Final blocker and implement verified claim-block events plus a terminal completion marker; raw or partial model tokens remain forbidden.

## Proposed Final execution order

1. **Trust/accountability quick gate:** CXR-06, then begin CXR-05.
2. **Binding decision while code work proceeds:** CXR-04 through `/arch-finalize`.
3. **Clinical failure semantics:** CXR-09 and CXR-10.
4. **Completeness and scale:** CXR-11 informed by F6; CXR-07 storage/readiness topology.
5. **Rule blocker and observability hardening:** CXR-12 before F1/F9 expansion; calibrate CXR-13 after the CXR-05 trace shape stabilizes.
6. **Contract closeout:** accept/document atomic JSON for CXR-08, or explicitly budget the larger SSE implementation.

## Plan-feed checklist

- [ ] Every **Final-blocker** has an owner, target PR, and acceptance test in the Final plan.
- [ ] `/arch-finalize` owns CXR-04 and the recommended CXR-08 decision; implementation PRs do not silently rewrite the binding contract.
- [ ] F2/F3/F4/F7 depend on the trace evidence produced by CXR-05; use those measurements to calibrate CXR-13 latency isolation without making asynchronous export a prerequisite to the artifacts.
- [ ] F1/F9 rule expansion does not outrun CXR-12 fallback parity.
- [ ] F6 load results set CXR-11 pagination budgets and CXR-07 replica/store capacity.
- [ ] Any deferred hardening item keeps its residual risk and acceptance test in the tracker.
