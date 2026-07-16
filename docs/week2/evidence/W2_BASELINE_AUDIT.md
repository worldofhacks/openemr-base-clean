# Week 2 closeout baseline audit

Recorded 2026-07-15 in `America/New_York` before closeout implementation.

| Item | Result |
|---|---|
| Audited base | `4f644d9bc69a30522f3f857f5aae6a768b7cf718` |
| Local `main` | exact audited base |
| GitHub `origin/main` after fetch | exact audited base |
| GitLab `gitlab/main` after fetch | exact audited base |
| Local/remote `swarm/w2-wave0` | exact audited base |
| Python suite | 663 passed, 5 skipped, 1 third-party deprecation warning; 8.45 s |
| Focused safety set | all selected tests passed; 0 failed |
| Focused set | numeric collision (`6.5` versus `65`), short-PHI scanner, patient route pin, exactly-once intents, document readback |
| Production `/health` | HTTP 200, `alive` |
| Production `/ready` | HTTP 200, `ready`; all reported hard and soft checks green |

The checkout already contained user-owned changes to `.claude/settings.local.json` and
`.gitignore`; closeout work preserves them. No fixture content, patient/user identifier,
token, prompt, transcript, or secret is recorded here.
