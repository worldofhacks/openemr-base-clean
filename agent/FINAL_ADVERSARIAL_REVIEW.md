# W2 Final Adversarial Review — Verdict

**Reviewer lane:** independent final adversarial pass over the integrated pipeline
(`swarm/w2-wave0 @ 880f8bb`, built by the parallel agent) against the frozen B1 schemas.
**Method:** trust nothing — every gate re-run locally, every claimed break independently
reproduced with an executable repro before it counted. Baseline reproduced: **628 passed /
7 skipped** across `tests evals ops bruno corpus` (0 failures; 7 skips are all live/ui/opt-in).

## Verdict: **PASS with two defects found and FIXED** (safety-critical). No open blocker.

Five safety invariants were attacked; each with a runnable repro, then adversarially
verified. Two broke and are fixed through the test loop (RED regression frozen first, no
existing frozen test weakened); three held.

| Invariant | Result | Evidence |
|---|---|---|
| **Grounding biconditional unbypassable** | **BROKE → FIXED** | CRITICAL, below |
| **no-PHI-in-logs** | **BROKE → FIXED** | HIGH, below |
| Composer: non-null page for guideline/document citations; no uncited claim; source-class separation | **PASS** | below |
| W2-D10 writeback: commit-then-timeout → unknown → stop; reconcile-before-retry; patient in ledger key; dedup no-op | **PASS** | below |
| Runtime fail-closed (deployed write disabled pending creds) | **PASS** | below |

---

## Defect 1 — GROUNDING numeric-collision (CRITICAL, W2-D3/§5) — FIXED

**Invariant broken:** an extracted value the page does not literally support could become a
grounded fact. `_normalize` in `app/grounding/verifier.py` stripped ALL non-alphanumerics
(`_NON_ALNUM = [^0-9a-z]+`), collapsing numerically distinct values: `6.5`↔`65` (a 10x
error), `-5`↔`5` (sign lost), `98.6`↔`986`, `1000`↔`1,000`, `0.5`↔`05`.

**Reproduced (mine, firsthand):** `ground_value(value="6.5", page words=["Result","65","unit"])`
→ `grounded=True`, `value="6.5"`, `citation.quote_or_value="65"`, `bbox` set. Reachable
end-to-end: `pipeline._extract → _reground → ground_value` feeds the raw VLM value unchanged,
and `build_vital_writes` (gate = grounded+citation+bbox) then emits a chart vitals write with
the 10x-wrong number. Grounding is the sole safety gate; nothing re-checks the value against
the page quote downstream.

**Fix (`94a8e3c`):** `_normalize` now preserves numerically-significant `.` and `-` and
canonicalizes a thousands comma, so the value tokenizer and the page tokenizer agree only on
faithful numbers. Every collision → UNSUPPORTED (`grounded=False`, `citation=None`); faithful
matches still ground; `1000`==`1,000` still matches. RED regression frozen first
(`tests/test_grounding_numeric_collision.py`). Codex's `test_grounding_verifier.py` stays green.

## Defect 2 — no-PHI-in-logs scanner misses short PHI (HIGH, W2-D7/§7) — FIXED

**Invariant broken:** the generated-output leak scanner (`evals/canary.py`) matched only the
ZZPHI canary, emails, and ≥12-char multi-word phrases, so a generated-output leak of a DOB,
MRN, extracted clinical value, or short name (`John Smith` is 10 chars) passed the
100%-required `no_phi_in_logs` gate.

**Reproduced (mine, firsthand):** planting `dob=1987-04-12`, `MRN-99887`, `glucose 92`, a
short name into generated logs/traces/results/observation → scanner reported `clean=True`.

**Fix (`9cb5b6d`):** added distinctive short-PHI signatures (dates, multi-digit values/MRNs,
name/contact identifiers) matched with numeric-safe boundaries so a leak is caught but
`92ms`/`9200us`/`1987` do not false-fire; single-char codes (sex) are deliberately skipped to
keep the 100% gate stable. Repro + regression frozen (`evals/test_canary_short_phi.py`); the
40 legit golden cases still pass. (My adversarial golden cases 41-50 add the first
`no_phi_in_logs`-mapped cases to the set — the rubric was previously unexercised.)

## PASS — Composer (W2-D6/§2a/§5)

All four render invariants hold on the real path: `verify_then_render` drops any claim with
`citation is None`; `_has_required_location` requires a non-empty `page_or_section` for
guideline/uploaded_document citations (None/empty/whitespace all render 0) while allowing
null only for `patient_record`; `source_class` is always the citation's `source_type`
(unblurrable through the gate); ungrounded fields are omitted, never rendered as cited facts.
*Recorded low (defense-in-depth only, unreachable through production workers):*
`verify_then_render` trusts a hand-built `CandidateClaim.text`/citation binding and does not
re-derive text from the citation or check live-corpus resolvability — no production worker
emits a bare `CandidateClaim`, so not reachable. Suggested belt-and-suspenders: assert
`claim.text == citation.quote_or_value` on the passthrough path.

## PASS — W2-D10 exactly-once writeback (§3)

Commit-then-timeout moves the intent to `unknown` and never blind-retries; reconcile-before-
retry lists/re-reads the remote by pinned patient + content/payload hash before any re-POST;
conflicting/multiple matches fail closed; duplicate re-upload is a no-op (one logical job,
one remote object); `patient_id` is in the permanent ledger key so identical bytes for two
patients stay distinct; a returned remote id still requires a verifying readback before
COMPLETE. *Recorded low:* the intent state machine has no CAS/row-lock, so two concurrent
executes of the SAME intent could double-POST — already mitigated in production by the
durable queue's `FOR UPDATE SKIP LOCKED` lease (one intent → one worker); recommend a CAS on
the intent row as hardening.

## PASS — Runtime fails closed (W2-D1/D9)

The deployed write runtime is disabled without owner-provisioned delegated creds and cannot
be tricked open: the config gate rejects partial attestation at boot; the disabled
composition never wires the write facade (`AttributeError`, zero remote calls); credential
resolution refuses to mint a principal without valid delegated material; the grant layer
forbids `client_credentials`/system auth; the scope guard blocks under-granted delegations;
readiness reports `disabled`/`503` rather than advertising ready. No path reached a real
write or a fake success without valid delegated creds + the full granted W2 scope manifest.

---

## Filter-blocked pieces folded in (only I can do)

- **Two-tier CI + fork-PR secret policy** (`a90fbf3`, `.github/workflows/agent-eval-gate.yml`):
  Tier-1 offline PR-blocking (forks included, no secrets); Tier-2 live 50-case graded gate,
  same-repo only, least-privilege environment, fail-closed STOP-escalation until W2-OA2 — never
  `pull_request_target`. Passes the W2-M24 `lint_workflows`/`lint_policy_doc` (77 tests green).
- **Adversarial golden cases 41-50** (`678704b`): 10 injection cases + reproducible fixtures,
  appended to `cases.json` without renumbering Codex's 1-40; first `adversarial` and first
  `no_phi_in_logs` cases in the set.

## TODO — re-verify against the LIVE deployed write path (owner action W2-OA2/OA3)

The writeback / cross-patient / scope / attribution containment above was proven against the
LOCAL pipeline and the deployed **fail-closed** posture (`document_runtime: disabled`). Once
the owner provisions the delegated credentials and activates the write path, **re-run the
adversarial containment checks against the LIVE deployed surface**: cross-patient isolation
(patient in ledger key, no wrong-patient write), exact granted-scope assertion + 403 on
missing `api:oemr`, caller-attribution stripping (W2-F16 — no request-body performer), and
the commit-then-timeout → unknown → reconcile path against the real OpenEMR remote (no blind
re-POST). Also re-run Tier-2 (live 50-case) once `ANTHROPIC_API_KEY` lands.
