# PROJECT_STORY.md — Clinical Co-Pilot for OpenEMR

> The synthesized, sequential narrative of the project — what was set out, decided, found, changed, and shipped, in order and with the *why*. Regenerated from `docs/DEVLOG.md` + git + the ADRs. Every claim cites a decision (D#), finding (F#), research note (R#), or commit so it's traceable. For the raw dated record see `docs/DEVLOG.md`; for the decisions themselves see `docs/planning/DECISIONS.md` and `AUDIT.md`.
>
> Last synthesized: 2026-07-12.

---

## The arc, in one paragraph

The task was to build an AI *Clinical Co-Pilot* on top of a real EHR — a read-only, multi-turn agent that gives a primary-care physician a **verified, cited pre-visit brief** in the 90 seconds between exam rooms. We started from a pruned fork of OpenEMR master (`ef3d490`), turned the brief into research-backed ADRs, and ran a read-only forensic audit before agent code. Its decisive discoveries were that inherited FHIR fields can misstate clinical status (F-D.1) and OpenEMR cannot attribute API calls to the OAuth client/scopes (F-C.1/F-C.2). Those findings made the deterministic verification layer (§5) and Langfuse accountability record (D5/§7) load-bearing. The finished sidecar now launches from one SMART chart button, performs six delegated read-only FHIR calls, makes the model answer in typed cited claims, verifies each claim, and renders only verified fields; it has durable session pins, a scrollable UI, grounded prompts, deployment hardening, load/cost evidence, alerts, Bruno flows, and a 10-case deploy gate. D16 adds a default-off content policy for synthetic-only trace debugging plus PHI-free live scores and Langfuse-native scored Dataset runs, explicitly pending owner architecture-finalize sign-off. The live José path, full suite, offline eval gate, both remotes, and Railway readiness are the submission evidence; real PHI remains out of scope.

---

## Phase 0 — Genesis (2026-06-27)

The repo began as a **pruned import of OpenEMR master, v8.2.0-dev** (`ef3d490`) — the full application with git history stripped to a single commit. This detail matters more than it looks: everything the later audit found is *stock upstream OpenEMR* behavior, inherited verbatim, not something the fork introduced. That framing — "these are constraints to design around, not bugs to fix" — runs through the whole project.

## Phase 1 — Planning: turning a brief into defensible decisions (2026-07-06)

Rather than start coding, the first working day was spent on an interview-gated architecture playbook that produced the planning artifact set: `PRESEARCH.md`, `RESEARCH.md` (R1–R11), the ADR log `DECISIONS.md` (D1–D13), a §-anchored draft, and a defense script (committed later in `bf26da4`). The PRD's own rules drove this: every capability must trace to a use case, and the audit is a hard gate before any AI code.

The load-bearing choices:

- **Who (D1):** a primary-care physician with a 20-patient day. Not arbitrary — it's the *only* persona expressible in both OpenEMR's ambulatory data model and the sanctioned Synthea demo data without fabricating clinical context (R10). An ED or hospitalist persona would have forced invented data, violating the PRD's grounding principle on day one.
- **Where (D2):** an **external SMART-on-FHIR sidecar**, not code embedded in OpenEMR. This is the single most defensible decision, because it makes the answer to "where are the trust boundaries and how are they enforced?" be *"in OpenEMR's own certified authorization layer — we didn't build a parallel one to get wrong."* ONC §170.315(g)(10) makes SMART-on-FHIR the federally *mandated* integration pattern (R5), which also makes the agent portable to any certified EHR.
- **How (D3/D4/D6/D9/D10/D7):** Python + FastAPI + Pydantic (R9); Claude Sonnet 4.6 + Haiku behind one `llm.complete()` seam (R1); a direct Anthropic tool-use loop, no framework (R6); FHIR-API-only reads with the delegated token, never SQL (D9 — SQL would forfeit D2's whole defense); parallel fan-out of the six independent reads (D10); and — the crown jewel — a **verification layer** (D7) where every tool result becomes an EvidencePacket of typed evidence, the model answers in typed claims carrying evidence IDs, a verifier rejects on field-level contradiction, and a deterministic templater re-renders the physician's text from verified fields so *the model cannot phrase its way past a check*. That design is backed by literature: ungrounded medical LLMs hallucinate >60% with 45%+ fabricated references (R7).
- **Safety (D12):** read-only *by construction*, a deceased-indicator hard-stop, and refusal-as-a-feature — because in a clinical setting a confident wrong answer is the trust-killing failure mode.

Two reversals happened on this same day, and they're worth their own callouts (below): **D8** flipped the deployment target from a VPS to Railway, and **D11** cut voice I/O after research falsified its premise.

## Phase 2 — Deploy the fork (Stage 1 + 2, 2026-07-07)

Stage 1 was local: bring up the dev stack and load **25 Synthea patients** via the repo's sanctioned devtool (1,042 encounters, 152 meds, 41 allergies, 4,101 labs) — a realistic outpatient panel, synthetic only.

Stage 2 was the interesting part. The **image-path decision** was to build from the fork's own checkout (`docker/railway/Dockerfile`, replacing the upstream `git clone` with `COPY . /openemr`) because the fork is pruned master with no matching release tag — every official image would ship *upstream's* code, not the fork's. Then came the pathfinding: Railway's builder **rejected the `VOLUME` instruction and unqualified cache mounts** that vanilla BuildKit accepts (attempts 1–2), and once it built, a Railway **healthcheck false-failed a provably-healthy app** (attempt 3: 543 s clean boot, Apache binding `:::80`, `/readyz` returning 200 — yet marked FAILED). The fix was a small pivot — remove the healthcheck, verify end-to-end against the public URL, lean on the restart policy (attempt 4 succeeded). The fork went **live at https://openemr-production-cc95.up.railway.app** with a real security baseline: a generated admin password (never a default-cred window), FHIR metadata serving, no debug surfaces exposed.

To seed prod we dumped the local DB and imported it over Railway's MySQL proxy (excluding `users_secure` so the local password hash couldn't ride in). That import later turned out to carry a **hidden landmine** — see the OAuth crypto break in Phase 4.

## Phase 3 — The audit: the hard gate that reshaped the architecture (Stage 3, 2026-07-07)

Before any agent code, we ran a **read-only forensic audit** (`AUDIT.md`, `c51d6db`) — five sections, opening with a ~500-word summary, every finding tagged with file:line evidence and mapped to the decision it affects. Its distinguishing discipline: **every critical/high finding was independently re-checked by an adversarial refuter.** That's what makes the audit trustworthy, and it materially changed the results.

What it found that reshaped the build:

- **F-D.1 — the immunization inversion.** A case-sensitive `== "Completed"` against lowercase DB data makes *every completed vaccine* render as `not-done` + "patient objection" (67/67 for the canonical patient). A naïve agent would tell a physician the patient refused every vaccine. This single finding is the concrete justification for the entire §5 verification layer — proof it's load-bearing, not theater.
- **F-C.1 + F-C.2 — the un-attributable, un-joinable audit log.** OpenEMR's `api_log` omits the OAuth `client_id` and granted scopes, and has no correlation-id column or header-capture path. So OpenEMR literally cannot answer "which app, under which grant, made this call," and the planned cross-system trace join is impossible. This forced the D10 revision and *elevated* D5.
- **F-S.4 / F-D.4 / F-A.2 / F-S.5 / F-P.1** — PHI stored plaintext in `api_log` by default (a second in-boundary PHI store → D15); allergy criticality null dataset-wide (→ §5 rule); D2's SMART/OAuth surface *confirmed* real with S256-enforced PKCE (F-A.2); `authorization_code` attributes to the clinician while `client_credentials` erases the human actor (F-S.5); and a ~0.39 s per-read latency floor from uncached schema introspection (F-P.1).

And the refuter earned its keep by overturning the audit's *own* first drafts: it proved an ACL "smoking gun" **factually wrong** (F-S.1 — ACL restrictions *are* registered), refuted a PHI-egress "breach" that described *unbuilt* design as shipped (F-C.4), and downgraded several data-quality findings to demo-data noise (F-D.6/F-D.2). Those corrections are the most valuable part of the audit — they show the process caught its own errors.

The audit fed straight back into the ADRs (`de4e5bc`): D2 reworded (scopes + compartment binding, not scope∧ACL), D7 given six concrete verifier rules, D12 given a synthetic-fixture requirement (the demo data has zero deceased patients, so the hard-stop would otherwise ship untested), and two new decisions added — D14 (user-scoped OAuth apps register *disabled*) and D15 (`api_log` is a PHI store).

## Phase 4 — Finalize + a production-down API hiding behind a working login (Stage 4/5, 2026-07-07)

`USERS.md` (Stage 4, `7f4aaeb`) pinned the four use cases each with a "why a conversational agent" trace. Then `ARCHITECTURE.md` (Stage 5, `33cc5bb`) was finalized through a **cold-eyes gap audit** across 12 dimensions — a coverage table with zero blank cells, 16 findings resolved without needing a fork-in-the-road decision, opening with a 524-word summary and citing D#/F#/R#/UC# throughout. It honestly folds in both the audit's confirmations (D2/D9 sound) *and* its challenges (the withdrawn api_log join), and uses F-D.1 as the concrete reason §5 exists.

In parallel, seeding prod bit back. The cross-instance DB import had **overwritten prod's master encryption keys** with the local instance's, so prod could no longer decrypt its own drive-key files — every OAuth2 token request 500'd. The tell that makes this a good story: **the web UI kept working** (passwords are bcrypt, independent of the drive key), so a production-down REST/FHIR API was completely invisible behind a working login. The fix was to wipe *both* halves of the crypto and let OpenEMR regenerate a consistent set (0 encrypted docs → no data loss), then register and enable a fresh OAuth client (`06fee47`, `DEPLOYMENT.md` §5/§8). The lesson is documented: the dump-and-import method should exclude the crypto/config tables.

## Phase 5 — Plan and build (2026-07-08)

`IMPLEMENTATION_PLAN.md` (`04dd59a`) decomposed the architecture into 31 §-anchored tasks against the real deadlines — EARLY (a live, verified, observable agent doing the pre-visit brief end-to-end) and FINAL (full verification, dashboard, load tests, cost model, deploy hardening) — every task carrying acceptance criteria *including* the edge/error behavior, with observability and trust-boundary work ordered before features.

Then the first application code: **E1**, built test-first with observability *first* (per §7). E1.1 a FastAPI skeleton with **fail-fast** typed config (a missing secret fails at boot, not as a request-time 500). E1.2 a `/health` liveness endpoint and a **real** `/ready` that probes hard dependencies (OpenEMR FHIR metadata, Anthropic, session store) → 503, and treats Langfuse as a soft dependency → 200 `degraded` — no unconditional 200. E1.3 JSON logging + a correlation-ID middleware that threads an ID through every log line and outbound call. 19 tests green, and — the satisfying part — verified against the *live* OpenEMR, where `/ready` correctly returned 503 with genuine per-probe results. (An aside: the host's Python 3.14 was broken, so the venv runs on 3.12.)

## Phase 6 — Early build acceleration (2026-07-09 to 2026-07-10)

The submission repo was wired to the Gauntlet GitLab mirror and the agent then moved through the full Early chain: delegated SMART authorization and a clinician/patient session pin; typed six-tool FHIR fan-out; stable EvidencePacket IDs; direct Anthropic tool use and prompt caching; the deterministic verifier and verify-then-flush renderer; Langfuse accountability; the mixed-category eval gate; and a Railway `/chat` serving path. Each layer was landed test-first, and audit findings such as medication dosage shape (F-D.2), empty allergies (F-D.5), inactive conditions (F-D.6), and missing OAuth attribution (F-C.1) became executable contracts rather than documentation-only warnings.

## Phase 7 — Final: make the live path demonstrable (2026-07-10 to 2026-07-12)

Final work closed the gap between a safe backend and a credible physician demo. The all-blocked D13 path was bounded so a synthesis question could never dump a full chart; resolution questions were taught to cite inactive Condition records without claiming “cured.” PostgreSQL made the clinician/patient pin durable, while the bearer token intentionally stayed in-process so a restart fails closed into SMART re-launch (§3a). The UI gained citation chips, correctly separated labs from medications, made the conversation scroll on desktop and phone, and replaced speculative prompts with three questions proven against José’s actual FHIR chart. The obsolete SMART client was disabled, leaving one EHR launcher. Operational deliverables added the Langfuse dashboard/alerts, bounded k6 baselines, Bruno authenticated flow, real cost analysis, deployment hardening, and the social post. Deferred items—UC2–UC4, SSE, verifier v2 depth, and encrypted multi-replica token persistence—were named rather than disguised.

## Phase 8 — D16: observability that can answer “what happened?” (2026-07-12)

The first Langfuse integration proved accountability and timings but intentionally omitted clinical content, so a reviewer could not see what the model saw or compare its typed answer with the physician-visible verified brief. D16 separates those concerns. The production-correct policy stays minimum-necessary: `LANGFUSE_LOG_CONTENT=false`, with hashed patient/user accountability and PHI-free metrics. The Synthea-only Railway demo opts in through an environment flag. A single Langfuse v4 mask controls prompt, normalized tool result, submitted claim, raw completion, and served-brief disclosure; a malformed mask value fails closed, and score/export failures never touch serving (CXR-13/§6).

The same decision makes the eval claim visible in the tool instead of merely asserted. The ten deterministic cases are stable Dataset items, and each configured run reruns the real case pipeline as a linked trace with pass/fail and verifier accounting scores. Offline pytest/JSON remains the deploy authority, so Langfuse downtime cannot change a gate. The proof is concrete: 238 tests passed, the offline suite passed 10/10, Dataset run `4b7d5fdf-dfd4-4981-bc32-2e84cceeca21` contains ten scored traces, and José’s live request is trace `e81c974b3aa5aac45c631c5fb0c5c866` with exact prompt/verified output, all six FHIR contents, fourteen verifier claims, and six PHI-free request scores. D16 and the matching §7 architecture addendum remain proposed until the owner records architecture-finalize sign-off.

---

## The pivots, called out

These reversals are the proof the process was *reasoned*, not lucky — each is a decision changed by specific evidence, documented forward.

1. **VPS → Railway (D8, 2026-07-06).** *Evidence:* a one-week solo budget where engineering hours are the scarcest resource. *Change:* dropped the strongest raw-control/cost option (VPS + Compose + Caddy) because it spends the week on undifferentiated ops; Railway zeroes out TLS/domains/deploy/DB so the week goes to the graded agent.

2. **Voice I/O cut (D11, 2026-07-06).** *Evidence:* research R8 falsified the premise — browser `SpeechRecognition` routes audio to Google/Azure/Apple speech clouds, so "no new PHI trust zone" was false. *Change:* cut voice from week-1 scope rather than introduce an un-BAA'd speech-cloud PHI zone; documented a self-hosted-Whisper revisit path.

3. **api_log join withdrawn (D10, 2026-07-07).** *Evidence:* audit F-C.1/F-C.2 — `api_log` has no correlation column and no header-capture path, and D9's read-only rule forbids adding one. *Change:* withdrew the "trace reconstructable via a cross-system join" claim; Langfuse (D5) became the authoritative agent-side record; api_log correlation is best-effort/fuzzy only. Also spun off R12 (the 28 s latency figure re-tagged an unverified assumption).

4. **Self-hosted → Langfuse Cloud (D5, 2026-07-08).** *Evidence:* verified that Langfuse Cloud offers a BAA + a dedicated HIPAA data region (`hipaa.cloud.langfuse.com`), which dissolved the sole rationale for self-hosting ("only way to avoid an un-BAA'd third party"). *Change:* moved observability to Langfuse Cloud under an assumed BAA — the same posture as the LLM provider (D4) — cutting the self-hosted service group and the ClickHouse cost risk (D8-update), while *keeping* the elevated §164.312(b) accountability role. The MIT self-host exit is retained as the vendor-risk fallback.

*(A fifth, smaller reversal lives inside the audit itself: the adversarial refuter overturned three of the audit's own first-pass verdicts — F-S.1, F-C.4, F-D.6/F-D.2 — which is why the audit is trustworthy rather than just confident.)*

---

## Read it deeper

- Every dated entry with evidence: `docs/DEVLOG.md`.
- The decisions and their full defenses: `docs/planning/DECISIONS.md` (D1–D16; D16 pending owner finalize sign-off).
- The findings with file:line evidence: `AUDIT.md` (F-#).
- The sourced external facts: `docs/planning/RESEARCH.md` (R1–R12).
- The binding contract: `ARCHITECTURE.md` (§1–§11).
- The build plan: `IMPLEMENTATION_PLAN.md` (E1–E9 / FINAL).
