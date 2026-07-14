# W2-M3 implementation report — LangGraph skeleton + SSE spike

Ticket: `tickets/W2-M3.md` · Branch: `ticket/w2-m3-graph-skeleton` · Freeze SHA: `d88b234`
Status: DONE (all frozen tests green, all Tier-1 gates pass, AC-7/AC-8 live-measure evidence below).

## What changed

| File | Change |
|---|---|
| `agent/app/orchestrator/state.py` (NEW) | `SupervisorDecision` (closed enum, exactly the §2 five-member set), `ReasonCode` (closed enum with per-decision allowed sets enforced by a model validator), `HandoffRecord` (Pydantic v2, `extra="forbid"`, `strict=True`, frozen; all eight §2 fields), `GraphState` (per-turn TypedDict, `handoffs` accumulates via `operator.add`). |
| `agent/app/orchestrator/workers/__init__.py` + `stub_extractor.py` + `stub_retriever.py` (NEW) | Placeholder workers (replaced by W2-M9/M14). Each exposes `WORKER_NAME` + async `run(...)` returning a trace-addressable output ref — refs only, never values, cross the handoff boundary. |
| `agent/app/orchestrator/graph.py` (NEW) | `graph_enabled()` (env `W2_GRAPH_ENABLED` read per call; unset=OFF, `"1"`=ON), `run_graph_turn(...)` — the single graph entrypoint. LangGraph `StateGraph`: supervisor + extract + retrieve + compose nodes; conditional edges off the supervisor's decision; workers edge back to the supervisor; compiled **without a checkpointer**; per-turn state discarded. The W1 loop runs UNCHANGED inside the compose node (awaits the injected `run_brief`; `BriefResult` passed through object-identical — AC-3). Step budget 8 (§2 working value): the supervisor refuses at hop counter ≥ 8 with a terminal `reason_code=step_budget_exceeded` record and the W1-canonical refusal (`_DEFAULT_REFUSAL_TEXT` imported from `loop.py`, so the text can never fork). LangGraph `recursion_limit` set to `2*STEP_BUDGET+4` so the semantic budget always fires before the framework bound. When `tracer`+`accountability` are given, emits one Langfuse trace: `graph.supervisor` span ⊃ `graph.worker.stub_extractor` / `graph.worker.stub_retriever` / `graph.composer` spans, `session_id` = correlation id, PHI-minimized metadata (hashed user/patient, sanitized URL, refs, decisions). Soft dependency: any export failure increments `tracer.dropped`, never surfaces. |
| `agent/app/routes/chat.py` (EXTENDED behind the flag only) | §2a SSE opt-in: `Accept: text/event-stream` content negotiation on the same POST body, active only when `graph_enabled()` is true. The stream is served through `orchestrator_graph.run_graph_turn` (late-bound module-attribute access — see finding 2) after the unchanged session-pin/refusal checks, and carries one verified `{claim_block, citations[], verdict}` event (W1 §5a shape) then a terminal `done` event. Flag OFF (default): route is bit-identical W1 — same JSON envelope, same 404/401/503/403 mappings, graph never invoked (tripwire-proven). Flag ON without opt-in: W1 JSON contract unchanged. |

Untouched, per ticket: `agent/pyproject.toml`, `agent/Dockerfile`, `loop.py`, `app/observability/*`, all W1/W2 binding docs, all OpenEMR PHP.

## Gate evidence (final run)

```
GATE syntax: PASS
GATE unit-tests: PASS
252 passed, 6 skipped, 1 warning in 1.12s
GATE frozen-tests: PASS
spec-lint: W2-M3:AC-7 -> live-measure evidence row (exempt from frozen-test mapping)
spec-lint: W2-M3:AC-8 -> live-measure evidence row (exempt from frozen-test mapping)
GATE spec-lint: PASS
GATE no-todos: PASS
GATE no-debug: PASS
GATE no-skip-markers: PASS
----
ALL GATES PASS
```

Suite arithmetic: this branch/venv records **236 passed, 6 skipped** at the freeze commit with the new test file ignored (gates.md baseline on main was 238/5; the delta pre-exists here — playwright is not installed in this worktree venv, so `test_ui_smoke.py` import-skips). My changes add exactly the 16 frozen W2-M3 tests: 236 + 16 = **252 passed**; zero prior tests affected.

## AC-7 [live-measure] — REAL Langfuse span nesting

One graph turn with the synthetic metformin packet + fake `submit_claims` provider, but the **real** `LangfuseSink` (keys from the gitignored `agent/.env`, copied from the primary clone; content logging left at the D16 default = OFF; no secrets in code, logs, or this report).

- **correlation_id:** `w2m3-live-ac7-1784013711`
- **trace_id:** `52c7bfaf75d3b116d5fe080e7b417cb4` (trace name `graph-turn`, sessionId = the correlation id)
- **tracer.dropped:** 0
- **Nesting, read back via the Langfuse public API** (`parentObservationId` chains, not the SDK's word for it):

```
graph.supervisor            (parent: None)
├── graph.worker.stub_extractor   (parent: graph.supervisor)
├── graph.worker.stub_retriever   (parent: graph.supervisor)
└── graph.composer                (parent: graph.supervisor)
NESTING supervisor ⊃ workers: VERIFIED
```

- **Correlation-ID-only reconstruction:** the trace was located via `GET /api/public/traces?sessionId=w2m3-live-ac7-1784013711` alone; the supervisor span metadata carries `correlation_id` and the ordered decision list `['route_extract', 'route_retrieve', 'compose_answer', 'done']`; HandoffRecords sort totally by `turn`. Hop list of the live turn: `[(0, route_extract, stub_extractor), (1, route_retrieve, stub_retriever), (2, compose_answer, composer), (3, done, supervisor)]`, `brief.source=llm`.
- W1 D16 content-OFF posture unchanged: sink constructed without `log_content`; span payloads are refs/enums/hashes only.

## AC-8 [live-measure] — SSE token-streaming verdict

**Verdict: the §2a named fallback is invoked — stream only the final composer stage.** Token streaming *through* LangGraph workers is not a framework limitation: langgraph 1.2.9 exposes `astream(stream_mode="messages"/"custom")` and `get_stream_writer` for mid-node token/event emission. It is a **contract** limitation, twice over: (1) the W1 loop embedded unchanged in the compose worker (W2-D2) answers via a non-streaming `provider.complete()` + terminal `submit_claims`, so no token stream exists inside the worker to forward without modifying `loop.py` (out of scope this wave); (2) W1 §5 verify-then-flush forbids serving any token before its claim-block verifies — raw model tokens may never reach the stream regardless of transport.

**Perceived-latency cost (for the cost report; a latency cost, never a correctness cost):**

- TTFE (time to first SSE event) == total turn time: measured ratio **1.00** across 3 runs (e.g. TTFE 266–274 ms of which 250 ms was an injected artificial LLM delay). The first content event lands when the whole verified brief is ready — i.e. at the same moment the W1 JSON body would have landed. SSE therefore currently buys the UI an explicit terminal `done` marker and a claim-block shape, not earlier content. In production the perceived wait equals the full W1 turn latency (seconds, LLM-dominated).
- Graph machinery overhead: **~3.2 ms/turn** (per-turn StateGraph build + compile + routing + 2 stub hops; mean of 30 turns, fake provider) — negligible against LLM turns.

## Decisions

1. **Terminal records are emitted by the supervisor; routed-hop records by the worker node that completed the hop** — so `output_ref` is the worker's real artifact ref, and emission order equals hop order (AC-1/AC-2).
2. **Budget semantics:** the §2 budget (8) bounds the hop counter `turn`; the supervisor checks it before consulting policy, so an adversarial/looping policy yields ≤ 8 routed hops + 1 terminal refusal record (AC-5). A policy-returned `refuse` (no producer today) is treated as budget refusal — documented in code.
3. **W1-canonical refusal text is imported from `loop.py`** (`_DEFAULT_REFUSAL_TEXT`, read-only) rather than duplicated — the canonical string cannot drift.
4. **Span nesting drives the sink's own Langfuse client** (`LangfuseSink._get_client()`, read-only): the W1 `sink.emit(RequestTrace)` mapping produces a flat root+steps tree and cannot express supervisor ⊃ worker; reusing the sink's client keeps credentials + the D16 mask owned by the sink. Non-Langfuse sinks get no graph trace in this spike (documented in `graph.py`; see finding 3).
5. **SSE opt-in = Accept-header content negotiation; terminal = `event: done`** — the frozen tests' minimal completion of §2a/W1 §5a, encoded from the test contract header, not invented here.
6. **Flag ON without opt-in keeps the W1 JSON path entirely** (graph not engaged for JSON callers) — the narrowest reading of §2a "W1 contract unchanged" and of the frozen AC-6 non-opt-in test.

## Spike findings (should feed the plan)

1. **SSE verdict (AC-8):** plan W2 streaming UX around composer-stage claim-block streaming. Real perceived-latency wins require incremental per-claim-block verification + a streaming provider path in the loop — a deliberate later feature, not a transport switch; raw token streaming stays forbidden by §5 either way.
2. **Import-order hazard for flagged entrypoints:** an early-bound `from graph import run_graph_turn` in `chat.py` made a monkeypatched tripwire become chat's "original" binding when `chat` was first imported while the patch was active (test-ordering-dependent failure, reproduced and fixed). Late-bound module-attribute access (`orchestrator_graph.run_graph_turn(...)`) is the safe pattern — W2-M9/M14+ should wire worker entrypoints the same way.
3. **Observability seam gap:** `LangfuseSink` exposes no public client accessor and its `RequestTrace` mapping is flat, so graph span nesting reaches into `sink._get_client()` (read-only). The observability owner should promote a public nested-trace API (or client accessor) on the sink before the graph becomes default-ON; also decide what non-Langfuse sinks should receive for graph turns (today: nothing).
4. **Framework budget interplay:** LangGraph's `recursion_limit` must be set above the semantic §2 budget (here `2*STEP_BUDGET+4`) or the framework error masks the designed refusal — carry into W2-M9/M14.
5. **Baseline drift (env, not code):** this worktree venv reports 236/6 pre-impl vs gates.md's 238/5 main baseline (playwright absent → `test_ui_smoke` import-skips). Wave integration should reconcile the recorded baseline per environment.

## Hygiene

- Frozen tests untouched (`git diff d88b234..HEAD -- agent/tests/` empty; gate-verified).
- `agent/.env` copied for AC-7 remains gitignored (`git check-ignore` verified); no secret value was printed, logged, or committed; synthetic non-clinical data only.
- Scratch live-measure scripts lived in `/tmp` (outside the repo) and are deleted.
- No pyproject/Dockerfile/OpenEMR/binding-doc edits.
