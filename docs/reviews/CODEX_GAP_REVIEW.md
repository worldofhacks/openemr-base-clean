# Codex Gap Review — Clinical Co-Pilot

> **Original review snapshot:** `origin/main` at `04e7fc0` (2026-07-09)
> **Reconciled:** 2026-07-10 against `origin/main` at `6ca8d4f` (PR #5 merged), plus pushed hotfix commit `65f97f5` on `origin/hotfix/e6b-citation-maxtokens` (not yet in `main`).
> **Method:** static, read-only review of the binding architecture, dated ADR/architecture addenda, audit, implementation plan, agent code, tests, DEVLOG, and recent Git history. No tests, builds, live requests, or deployments were run for either review pass.
> **Purpose:** actionable triage for the active builder. This report changes no implementation and does not supersede `ARCHITECTURE.md`.

## Executive triage

Three of the original thirteen findings are now fixed in merged PR #5: all-blocked output now becomes honest D13 degradation, clinician identity comes from the freshly exchanged `id_token`, and F-D.2 medication order/plan pairs are de-duplicated. Ten findings remain open. A separate live-E2E hotfix fixes short-form citation resolution and `max_tokens` truncation in commit `65f97f5`; that commit is pushed but was not merged into `main` at this reconciliation snapshot.

The original evidence is retained below as the audit trail for what was observed at `04e7fc0`. Each finding's **Status** and **Closure/Revalidation** note is authoritative for current triage; current line references use `6ca8d4f` unless a different commit is named.

| ID | Status | Severity | Gate | Current disposition |
|---|---|---|---|---|
| CXR-01 | **FIXED** | High | **Early blocker** | Honest all-blocked → D13 path merged in PR #5 (`d44df40`) |
| CXR-02 | **FIXED** | High | **Early blocker** | Clinician `fhirUser`/`sub` derived from `id_token` in PR #5 (`d44df40`) |
| CXR-03 | **FIXED** | Medium | **Early blocker** | F-D.2 order/plan medication de-dup merged in PR #5 (`d44df40`) |
| CXR-04 | **STILL-OPEN** | High | **Early blocker** | `user/*` grant remains broader than the patient-compartment wording |
| CXR-05 | **STILL-OPEN** | High | **Early blocker** | Baseline FHIR fan-out still precedes the request trace |
| CXR-06 | **STILL-OPEN** | High | **Early blocker** | Granted-scope guard still has no production exchange-path call site |
| CXR-07 | **STILL-OPEN** | High | **Early blocker** | In-memory serving state still conflicts with the Postgres readiness probe |
| CXR-08 | **STILL-OPEN** | High | **Early blocker** | Route remains buffered JSON without surfaced citations/SSE/interruption semantics; internal citation hotfix is separate |
| CXR-09 | **STILL-OPEN** | Medium | Final | Session deadline still ignores actual token `expires_in` |
| CXR-10 | **STILL-OPEN** | High | Final | Mapper exceptions can still escape the per-tool partial-failure envelope |
| CXR-11 | **STILL-OPEN** | Medium | Final | Pagination, recency, ordering, and selection semantics remain undefined |
| CXR-12 | **STILL-OPEN** | Medium | Final | Generic D13 branches still render the packet without formal verifier execution |
| CXR-13 | **STILL-OPEN** | Medium | Final | Langfuse export and flush remain synchronous |

### Post-review live hotfix — citation resolution + output truncation — FIXED

- **Status:** **FIXED (committed; merge pending)** in `65f97f5` on `origin/hotfix/e6b-citation-maxtokens`. No PR existed and the commit was not in `main` at the reconciliation snapshot.
- `agent/app/evidence/packet.py:140-163` at `65f97f5` adds unique resolution for exact IDs, bare `hash8`, bare source IDs, and `ResourceType:source_id` forms.
- `agent/app/verify/verifier.py:201-211` and `agent/app/verify/verifier.py:414-424` at `65f97f5` use that resolver while preserving fail-closed behavior for unresolvable citations.
- `agent/app/config.py:39-42` at `65f97f5` raises the forced `submit_claims` output ceiling from 2,048 to 8,192 tokens so data-rich claim payloads are not lost to `stop_reason=max_tokens`.
- The commit records live validation on a 199-record packet (22 pass, 6 flagged, 19 blocked). It does not close CXR-08: the HTTP route still does not surface citation objects or implement the binding SSE/interruption contract.

## Findings

### CXR-01 — All-blocked claims can return an empty, falsely healthy response — FIXED

- **Status:** **FIXED** by `d44df40`, merged through [PR #5](https://github.com/worldofhacks/openemr-base-clean/pull/5) as `6ca8d4f`.
- **Severity:** High
- **Gate:** **Early blocker**
- **Touches:** D7, D13; UC1, UC3, UC4; `ARCHITECTURE.md` §5 and §6; block outcomes driven by F-D.1/F-D.2/F-D.4/F-D.5/F-D.6.

**Closure (2026-07-10)**

- `agent/app/orchestrator/loop.py:226-230` defines verified-content detection independently of packet notices.
- `agent/app/orchestrator/loop.py:359-369` and `agent/app/orchestrator/loop.py:394-402` route zero surviving claims into grounded supersession.
- `agent/app/orchestrator/loop.py:427-443` returns a non-empty `deterministic_fallback`, `degraded=True`, `fallback_kind="all_blocked"`, and preserves verdicts.
- `agent/tests/test_verifier_leniency.py:194-221` freezes the behavior; the dated D7 addenda are in `ARCHITECTURE.md:120` and `docs/planning/DECISIONS.md:127`.

**Original evidence (`04e7fc0`)**

- `agent/app/verify/templater.py:219-228` discards all `blocked` and `refused` results.
- `agent/app/verify/templater.py:230-243` then emits only packet notices and returns `""` when a populated packet has no notices.
- `agent/app/orchestrator/loop.py:367-381` returns that empty string as `source="llm"` and `degraded=False` without checking whether any claim survived verification.
- The non-`submit_claims` branch has the same outcome at `agent/app/orchestrator/loop.py:342-354`.
- `agent/app/routes/chat.py:71-77` serializes the result as a normal HTTP success.
- `ARCHITECTURE.md:149` requires an honest “couldn't verify” message plus incident logging and metering when verification blocks.
- `docs/DEVLOG.md:294-297` records that this is reachable with live data: Sonnet paraphrases labels, the strict verifier blocks the claims, and verified narration remains a follow-up.
- `agent/tests/test_templater_verified.py:146-155` checks that a contradicted value is absent, but does not require a non-empty refusal or fallback.

**Impact**

Unsupported content still does not leak, which is the correct safety default. However, the physician can receive a blank response labeled as a successful LLM answer. That violates the refusal-as-feature posture and makes the live UC1 path appear healthy when it delivered nothing.

**Triage acceptance**

When zero clinical lines survive verification, the response must be explicit and machine-readable: an honest verification failure/refusal or the documented deterministic fallback, with non-healthy status metadata and a traced/metered verdict. A packet notice may accompany it but must not be the only protection.

### CXR-02 — Clinician identity is hardcoded — FIXED

- **Status:** **FIXED** by `d44df40`, merged through [PR #5](https://github.com/worldofhacks/openemr-base-clean/pull/5) as `6ca8d4f`.
- **Severity:** High
- **Gate:** **Early blocker**
- **Touches:** D2, D5, D9, D12; F-S.2, F-S.5, F-C.1; `ARCHITECTURE.md` §3, §4, §6a, §7; all UCs.

**Closure (2026-07-10)**

- `agent/app/auth/smart_client.py:46-73` derives `fhirUser` (preferred) or `sub` from the freshly exchanged `id_token`; `agent/app/auth/smart_client.py:184-188` stores it as `TokenResponse.clinician_sub`.
- `agent/app/service.py:95-100` uses that identity for the clinician/patient session pin, and `agent/app/service.py:126-133` carries the pinned identity into accountability tracing.
- `agent/tests/test_verifier_leniency.py:253-270` freezes the identity path. A demo placeholder remains only when the token has no decodable identity; the primary hardcoded-identity defect is closed.

**Original evidence (`04e7fc0`)**

- `agent/app/service.py:95-99` creates every session with `clinician_sub="openemr-clinician"`.
- `agent/app/auth/smart_client.py:51-62` does not model an `id_token`.
- `agent/app/auth/smart_client.py:136-149` filters the token response down to declared fields, so any returned `id_token` is discarded.
- `agent/app/auth/scopes.py:3-4` and `agent/app/auth/scopes.py:32-35` state that `openid` is requested specifically to identify the clinician for the D12 pin.
- `agent/app/service.py:124-130` uses the hardcoded session identity as the Langfuse accountability user, collapsing every clinician to one hash.
- `ARCHITECTURE.md:63` requires session creation pinned to the launching clinician and patient; `IMPLEMENTATION_PLAN.md:58-62` marks that task complete.

**Impact**

The delegated token still reaches OpenEMR as the real user, but the agent's own enforcement and audit record are not clinician-specific. Sessions are patient-pinned only, and the D5/F-C.1 trace cannot answer which clinician used the agent.

**Triage acceptance**

Derive and validate the clinician identity from the delegated OIDC result, seed the session once from that identity, and carry the same identity into the PHI-minimized accountability trace. Add a multi-clinician test that proves identities do not collapse.

### CXR-03 — F-D.2 order/plan medication de-duplication is absent — FIXED

- **Status:** **FIXED** by `d44df40`, merged through [PR #5](https://github.com/worldofhacks/openemr-base-clean/pull/5) as `6ca8d4f`.
- **Severity:** Medium
- **Gate:** **Early blocker** because E6.2 is checked complete
- **Touches:** D7; F-D.2; UC1, UC2; `ARCHITECTURE.md` §5 rule 6 and §8; E6.2.

**Closure (2026-07-10)**

- `agent/app/evidence/packet.py:57-100` defines deterministic medication identity (`rxnorm`, else normalized name), prefers `order` over `plan`, and preserves distinct drugs.
- `agent/app/evidence/packet.py:187-189` de-duplicates before trimming/counting at the EvidencePacket boundary.
- `agent/tests/test_verifier_leniency.py:286-322` freezes the order/plan pair behavior; `IMPLEMENTATION_PLAN.md:106` and the dated D7 addendum explicitly correct the earlier false completion.

**Original evidence (`04e7fc0`)**

- `agent/app/tools/contracts.py:59-66` retains medication `intent` and says it is used for de-duplication.
- `agent/app/tools/fhir_tools.py:87-100` maps each `order` or `plan` as a separate `MedicationRecord`.
- `agent/app/tools/fhir_tools.py:168-170` returns the raw MedicationRequest search result without semantic consolidation.
- `agent/app/evidence/packet.py:141-155` emits every retained record; it performs no medication grouping.
- `agent/app/evidence/packet.py:102-118` disambiguates duplicate IDs by creating more unique IDs, which preserves duplicates rather than consolidating them.
- `AUDIT.md:150`, `docs/planning/DECISIONS.md:82`, `ARCHITECTURE.md:116`, and `IMPLEMENTATION_PLAN.md:106-110` require one stable evidence record per order/plan drug pair.
- `docs/DEVLOG.md:251-254` records the live canonical result as 18 medication records—the audited 9 order + 9 plan representation.
- `agent/tests/test_verifier.py:263-278` has a de-duplication heading but tests only absent-dose behavior; `agent/tests/test_evidence_packet.py:73-98` tests ID collision handling, not semantic drug de-duplication.

**Impact**

The same nine drugs can reach the LLM and fallback renderer as eighteen medication lines. That adds noise in the physician's 90-second reading window and contradicts a checkmarked Early acceptance criterion.

**Triage acceptance**

Define the deterministic drug identity and precedence rules, consolidate order/plan pairs before the LLM/verifier boundary, preserve source provenance, and freeze the canonical 18-to-9 case as an F-D.2 regression.

### CXR-04 — `user/*` token breadth conflicts with the patient-compartment trust claim — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. `agent/app/auth/scopes.py:23-35` still requests `user/*`, while `ARCHITECTURE.md:86` still describes patient-scoped token containment; no 2026-07-10 addendum reconciles the two.
- **Severity:** High
- **Gate:** **Early blocker** requiring an architecture reconciliation
- **Touches:** D2, D9, D12, D14; F-S.1, F-S.2, F-S.6, F-C.5; `ARCHITECTURE.md` §4, §5a, §6a; all UCs.

**Evidence**

- `agent/app/auth/scopes.py:23-35` requests six `user/*.read` scopes, not `patient/*.read` scopes.
- `ARCHITECTURE.md:86` describes the agent's tokens as patient-scoped and relies on server-side single-patient compartment binding.
- `ARCHITECTURE.md:135` simultaneously documents the actual `user/*` scope set.
- `AUDIT.md:51-53` states that OpenEMR's compartment hard-lock applies only to `patient/<resource>` requests; `user/*` requests take the clinician ACL branch.
- `docs/planning/DECISIONS.md:98-100` and `docs/planning/DECISIONS.md:130-132` confirm that `user/*` is the deliberate implemented posture.
- Current serving is mitigated at the application layer: `agent/app/service.py:119-123` always supplies `session.patient_id`, and `agent/app/tools/fhir_tools.py:157-188` threads that patient into the six reads.

**Impact**

The agent-side pin constrains the current tool code, but the OAuth token itself is broader than the binding contract claims. `launch/patient` supplies context; it does not turn a `user/*` grant into a server-enforced patient-compartment grant. The current D2 defense therefore overstates containment if the token or FHIR client seam is misused. No direct serving-route bypass was demonstrated in this review.

**Triage acceptance**

The owner must explicitly choose and document the real model: clinician `user/*` scopes plus an agent-enforced patient pin, or a genuinely patient-scoped grant. Record the residual token-breadth risk and route the binding text change through `/arch-finalize`; do not silently edit `ARCHITECTURE.md` in implementation work.

### CXR-05 — Langfuse tracing starts after the six baseline FHIR reads — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. `agent/app/service.py:121-139` still completes fan-out before entering the traced orchestrator, and `agent/app/tools/fhir_client.py:43-62` still emits only the correlation header.
- **Severity:** High
- **Gate:** **Early blocker**
- **Touches:** D5, D10; F-C.1, F-C.2; UC1 and the all-UC traceability row; `ARCHITECTURE.md` §3.1 and §7; E7.1.

**Evidence**

- `agent/app/service.py:119-123` completes the six-read fan-out and builds the EvidencePacket before constructing accountability context.
- `agent/app/service.py:124-137` only then enters the orchestrator with the tracer.
- `agent/app/orchestrator/loop.py:247-254` begins the trace inside `run_previsit_brief`, after fan-out has completed.
- `agent/app/service.py:133-137` passes an empty `ToolRegistry`, so the generic tool spans at `agent/app/orchestrator/loop.py:391-397` cannot represent the six baseline reads.
- `agent/app/tools/fhir_client.py:43-58` propagates a correlation header but emits no per-call trace/accountability event.
- `agent/app/service.py:128` records the `/chat` URL, not each FHIR resource URL.
- `ARCHITECTURE.md:60` requires the correlation ID on every tool call and Langfuse span; `ARCHITECTURE.md:171` requires the accountability tuple per FHIR call.
- `AUDIT.md:167-169` makes Langfuse the only complete F-C.1 accountability record, while `IMPLEMENTATION_PLAN.md:118-122` marks E7.1 complete.
- `agent/tests/test_orchestrator_trace.py:75-92` proves spans for a mocked in-loop tool path, not the pre-orchestrator fan-out used by `AgentServices`.

**Impact**

The trace captures LLM and verification work but omits the six PHI reads, their resource URLs, individual timings, outcomes, and per-call accountability records. It cannot currently reconstruct which FHIR calls touched PHI or localize FHIR latency as §7 promises.

**Triage acceptance**

Start the accountable trace before the first FHIR read and emit a span/record for every real outbound FHIR call, including failures and requests that never reach the orchestrator. Preserve PHI minimization and the D10 no-hard-join limitation.

### CXR-06 — The granted-scope guard is not wired into token exchange — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. The guard remains defined at `agent/app/auth/scopes.py:51-58`, but `agent/app/service.py:20` imports only `requested_scope_string` and `agent/app/service.py:91-101` creates the session without enforcing granted-scope coverage.
- **Severity:** High
- **Gate:** **Early blocker**
- **Touches:** D9; F-C.5; `ARCHITECTURE.md` §4 and §5a; E3 scope gate.

**Evidence**

- `agent/app/auth/scopes.py:16-18` claims the runtime guard fails early at token exchange.
- `agent/app/auth/scopes.py:51-58` implements `assert_required_scopes_granted`.
- `agent/app/service.py:20` imports only `requested_scope_string` from the scope module.
- `agent/app/service.py:86-100` exchanges and stores the token without calling the guard.
- A repository search finds production call sites only in the definition; callers are confined to `agent/tests/test_scopes.py:48-55`, `agent/tests/test_smart_live.py:114`, and `agent/evals/cases.py:75-78`.
- `docs/planning/DECISIONS.md:98` says the runtime guard is enforced at token exchange.

**Impact**

If OpenEMR grants fewer scopes than requested, launch succeeds and the missing resources fail later as tool errors. That converts a grant/consent problem into a misleading partial clinical answer.

**Triage acceptance**

Fail the callback before session creation when any required scope is absent, with a non-secret error naming the missing scopes and a test through the actual callback/service path.

### CXR-07 — In-memory serving state conflicts with Postgres readiness and fail-closed claims — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. `agent/app/service.py:58-61` still composes in-memory session/token state while `agent/app/health.py:71-86` probes the unused Postgres endpoint as a hard dependency; `docs/DEVLOG.md:297` still records this as outstanding.
- **Severity:** High
- **Gate:** **Early blocker**
- **Touches:** D8, D12, O2; F-S.2; `ARCHITECTURE.md` §3a, §6, §6a, §7; E2.2, E9.1.

**Evidence**

- `agent/app/service.py:9-12` describes Postgres as the production path, but `agent/app/service.py:58-61` always constructs `InMemorySessionStore` plus in-memory token and PKCE dictionaries.
- `agent/app/health.py:71-86` declares the configured Postgres endpoint a hard readiness dependency and checks only TCP reachability.
- `ARCHITECTURE.md:150` requires fail-closed behavior when the session store is unavailable.
- `IMPLEMENTATION_PLAN.md:147-151` marks E9.1 complete while requiring session-store DB wiring and a green `/ready`.
- `docs/DEVLOG.md:294-297` explicitly records the deployed placeholder DSN as down and the real Postgres store as an outstanding owner step.

**Impact**

The process can serve from memory while `/ready` reports not ready because an unused backend is down. A restart destroys all sessions and delegated-token mappings, and the deployed path does not exercise the Postgres fail-closed implementation that the trust model relies on.

**Triage acceptance**

Align composition and readiness around the same store. Either wire the binding Postgres path and prove fail-closed behavior live, or explicitly reclassify the demo posture through the planning process and stop probing an unused hard dependency.

### CXR-08 — `/chat` does not implement the cited SSE contract — STILL-OPEN

- **Status:** **STILL-OPEN** at committed `main` (`6ca8d4f`). `agent/app/routes/chat.py:37-42` and `agent/app/routes/chat.py:50-78` remain buffered JSON without surfaced citations; `agent/app/llm/provider.py:164-181` remains non-streaming; E9.2 remains unchecked.
- **Partial closure:** hotfix `65f97f5` fixes the model-to-packet citation-ID resolution and 2,048-token truncation discovered live. It does not add route-level citation objects, SSE claim blocks, completion events, or interrupted-stream semantics.
- **Severity:** High
- **Gate:** **Early blocker** already represented by unchecked E9.2
- **Touches:** D7, D10; UC1, UC3; `ARCHITECTURE.md` §3, §5, §5a, §6; E9.2.

**Evidence**

- `agent/app/routes/chat.py:37-42` defines one buffered JSON `ChatResponse` with no citations field.
- `agent/app/routes/chat.py:50-77` awaits the complete brief and returns one response; there is no event generator or `StreamingResponse`.
- `agent/app/llm/provider.py:164-181` uses the non-streaming `messages.create` API.
- `agent/app/verify/verifier.py:61-65` retains matched evidence IDs internally, but the route does not expose them.
- A repository search finds no `text/event-stream`, incomplete-stream marker, or `test_stream_interruption_marks_incomplete` implementation.
- `ARCHITECTURE.md:63-66` requires verified claim-block streaming with citation chips; `ARCHITECTURE.md:134` defines `{claim_block, citations[], verdict}` SSE events; `ARCHITECTURE.md:146` defines interrupted-stream behavior.
- `IMPLEMENTATION_PLAN.md:152-156` correctly leaves E9.2 unchecked.

**Impact**

The buffered response is safe from partial unverified-token leakage, but it does not meet perceived-latency, citation, or interruption requirements. The physician cannot navigate claim provenance from the normal response.

**Triage acceptance**

Emit only complete verified claim blocks with resolved citation IDs, terminate with an explicit completion event, and mark interrupted streams incomplete. Preserve the invariant that raw model tokens never reach the client.

### CXR-09 — Session lifetime ignores the token's actual expiry — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. `agent/app/auth/smart_client.py:91` models `expires_in`, but `agent/app/service.py:104-106` still computes the deadline from configured `token_lifetime_seconds`; a FHIR 401 remains a generic non-200 failure.
- **Severity:** Medium
- **Gate:** Final
- **Touches:** D9, D12; F-P.5; `ARCHITECTURE.md` §3a and §6.

**Evidence**

- `agent/app/auth/smart_client.py:57-62` parses `expires_in` from the token response.
- `agent/app/service.py:95-104` ignores it and derives `token_expires_at` from configured `token_lifetime_seconds`.
- `agent/app/tools/fhir_client.py:57-58` converts every non-200, including 401, into an undifferentiated `FhirCallError`.
- `agent/app/tools/fhir_tools.py:148-154` then turns that error into ordinary partial-data absence.
- `docs/planning/DECISIONS.md:98-101` says a mid-session expiry prompts re-launch because `offline_access` was deliberately dropped.
- `ARCHITECTURE.md:142` still contains the superseded refresh-token branch, an acknowledged `/arch-finalize` reconciliation item.

**Impact**

A session can outlive the delegated token and misreport six authorization failures as missing clinical data rather than requiring re-launch.

**Triage acceptance**

Bound the session to the actual token expiry, distinguish FHIR 401 from data/tool failure, and return the canonical re-launch outcome. Reconcile the stale architecture row through `/arch-finalize`.

### CXR-10 — Mapper exceptions can abort the whole fan-out — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. Mapping still occurs outside the protected network-read block at `agent/app/tools/fhir_tools.py:146-154`, the bounded wrapper catches only timeout at `agent/app/tools/fhir_tools.py:209-214`, and `task.result()` can still re-raise at `agent/app/tools/fhir_tools.py:219-221`.
- **Severity:** High
- **Gate:** Final
- **Touches:** D10; F3; `ARCHITECTURE.md` §6; UC1.

**Evidence**

- `agent/app/tools/fhir_tools.py:148-152` catches exceptions only around the network search.
- `agent/app/tools/fhir_tools.py:153-154` performs resource mapping outside that `try` block.
- `agent/app/tools/fhir_tools.py:209-214` catches only `asyncio.TimeoutError` in the per-call wrapper.
- `agent/app/tools/fhir_tools.py:219-225` calls `task.result()` without converting other task exceptions into a named `FAILED` result.
- `ARCHITECTURE.md:141` requires every FHIR failure to become a partial answer that names what is missing.

**Impact**

One unexpected or malformed resource shape can escape the tool envelope and abort all six results, defeating the designed partial-answer behavior.

**Triage acceptance**

Normalize mapping/validation failures at the individual tool boundary, preserve the other five results, name the missing category without leaking raw payloads, and freeze an arbitrary malformed-resource regression.

### CXR-11 — Pagination and recency semantics can silently omit relevant data — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. `agent/app/tools/fhir_client.py:43-68` still returns one Bundle, `agent/app/tools/fhir_tools.py:162-188` still uses fixed counts without ordering/lookback, and `agent/app/evidence/packet.py:187-196` still applies first-N trimming after medication de-duplication.
- **Severity:** Medium
- **Gate:** Final
- **Touches:** D7, D9, D10; F-D.6, F-P.3; UC1, UC3, UC4; `ARCHITECTURE.md` §5a and §6.

**Evidence**

- `agent/app/tools/fhir_client.py:43-62` returns one Bundle and does not follow FHIR `next` links.
- `agent/app/tools/fhir_tools.py:162-188` relies on fixed `_count` limits without explicit ordering.
- `agent/app/tools/contracts.py:119-122` declares `lookback_days`, but `agent/app/tools/fhir_tools.py:173-177` neither accepts nor applies it.
- `agent/app/evidence/packet.py:141-147` trims the first records returned, with no recency or clinical-priority policy.
- `AUDIT.md:146-148` requires stale lab dates to be surfaced and large/valueless observation behavior to be explicit.

**Impact**

Large charts can omit later pages without a notice, and an unsorted first-N trim can retain old records while dropping newer ones. A method named `get_recent_labs` currently means category-filtered labs, not a bounded recent window.

**Triage acceptance**

Define and test pagination bounds, deterministic sort/selection rules, and explicit truncation notices. Make “recent” a real date policy and preserve stale-date warnings rather than silently filtering history.

### CXR-12 — The D13 fallback bypasses the formal verifier — STILL-OPEN

- **Status:** **STILL-OPEN**, narrowed at `6ca8d4f`. The all-blocked path now verifies first and honestly degrades (CXR-01 fixed), but generic D13 branches at `agent/app/orchestrator/loop.py:318-337` and `agent/app/orchestrator/loop.py:423-457` still call the packet renderer directly; `agent/app/verify/templater.py:135-159` contains no `Verifier` execution.
- **Severity:** Medium
- **Gate:** Final
- **Touches:** D7, D13; F-D.1/F-D.2/F-D.4/F-D.5/F-D.6; `ARCHITECTURE.md` §5 and §6.

**Evidence**

- `agent/app/orchestrator/loop.py:406-417` sends the EvidencePacket directly to `render_packet_fallback`.
- `agent/app/verify/templater.py:135-159` renders packet fields directly and appends evidence IDs; no `Verifier` call occurs on this branch.
- `ARCHITECTURE.md:144`, `docs/planning/DECISIONS.md:127-128`, and `IMPLEMENTATION_PLAN.md:94-98` say the verifier still runs during D13 fallback.

**Impact**

The fallback is evidence-only and therefore safer than model prose, but it is not governed by the same formal verdict/constraint path promised by the contract. New verifier-only constraints can diverge from fallback rendering unnoticed.

**Triage acceptance**

Define what “verifier still runs” means for a no-LLM path, produce explicit verdicts/trace data, and freeze parity tests for every rule that applies to both normal and fallback output.

### CXR-13 — Langfuse export is synchronous on the serving path — STILL-OPEN

- **Status:** **STILL-OPEN** at `6ca8d4f`. `agent/app/observability/langfuse.py:75-112` still emits the span tree and calls `client.flush()` synchronously; trace finalization reaches that sink at `agent/app/observability/langfuse.py:140-180`.
- **Severity:** Medium
- **Gate:** Final
- **Touches:** D5, D10; `ARCHITECTURE.md` §6 and §7; F-P.5/R12 latency posture.

**Evidence**

- `agent/app/observability/langfuse.py:75-112` creates the remote span hierarchy and calls `client.flush()` synchronously.
- `agent/app/observability/langfuse.py:140-166` emits during trace finalization.
- `agent/app/observability/langfuse.py:178-182` swallows exceptions, but only after the synchronous sink call returns or fails.
- `ARCHITECTURE.md:152` says a Langfuse outage must leave serving unaffected because observability is off the critical path.

**Impact**

Exceptions do not fail the response, but a slow or hanging observability backend can still add user-visible latency before the route returns. “Soft dependency” currently means failure-isolated, not latency-isolated.

**Triage acceptance**

Bound or decouple export latency, retain a dropped/buffered counter, and add a slow-sink test proving the clinical response is not delayed beyond the defined observability budget.

## Tracker implications

- E7.1 should not be treated as fully closed until CXR-05 is resolved and E7.0 provisions a live sink.
- E6.2's previously missing F-D.2 semantic de-duplication is now closed by PR #5; the false completion is documented in `IMPLEMENTATION_PLAN.md:106`.
- E9.1 is deployed, but its stated Postgres/readiness acceptance is not met while CXR-07 remains.
- E9.2 is correctly unchecked; CXR-05, CXR-08, live Langfuse provisioning, and integration/redeployment of `65f97f5` remain on its boundary. CXR-01 is closed.
- Any change to the token authorization model or the refresh posture must go through `/arch-finalize`, not an implementation-only edit.

## Explicit non-findings

- No path was found that renders a contradicted or unresolvable model claim as verified clinical content. The former post-block blank-response failure, CXR-01, is now fixed by honest D13 degradation.
- The six serving tools are read-only FHIR GETs and the current service supplies the session's patient ID to them. CXR-04 is a token-containment/contract mismatch, not evidence of a current cross-patient request path.
- Langfuse sink exceptions are caught and counted. CXR-13 concerns synchronous latency, not exception propagation.
