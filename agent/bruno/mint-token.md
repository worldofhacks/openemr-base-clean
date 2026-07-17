# Dev-only SMART session mint helper

F5 calls this a token-mint helper, but the safe artifact for the deployed API is an
opaque agent `session_id`. The delegated OAuth access token must remain server-side.
A direct token exchange would not populate the agent's PKCE state, patient pin, token
cache, or session store, so `/chat` could not use it (§4, §7, D12, D14).

## Recommended Week 2 path (ordinary browser, no Selenium)

From `agent/`, run:

```bash
python3 bruno/dev_mint.py --manual --flow week2
```

The helper opens the deployed agent's `/week2/launch` in the default browser. Complete
the normal SMART login, synthetic-patient selection, and consent there, then copy the
final `/week2?sid=...` address from the browser into the helper's non-echoing prompt. The
helper validates the exact configured agent origin, exact `/week2` path, and single opaque
`sid` before writing `environments/Runtime.bru` with mode `0600`. It never receives an
OpenEMR password, and the session id is not placed on the command line or printed.

Then run the full collection:

```bash
cd bruno
npx --yes @usebruno/cli@3.5.2 run --env Runtime --bail
```

## Automated compatibility path

Without `--manual`, `dev_mint.py` retains the Week 1-compatible Selenium automation used
by the opt-in live E9 test:

1. Open the deployed agent's `/launch` endpoint in the repository Selenium grid.
2. Verify the redirect reached the configured OpenEMR HTTPS origin, then authenticate
   and select a synthetic Synthea patient.
3. Approve the enabled D14 SMART client.
4. Let the agent exchange the authorization code and create a patient-pinned session.
5. Capture and validate the callback's trusted `/app?sid=...` redirect. The helper allows
   that page to commit but blocks its automatic `/chat` network request, so the UI cannot
   consume one of the session's bounded turns.
6. Write only `agent_base_url` and `session_id` to `environments/Runtime.bru` with mode
   `0600`. That generated file is gitignored; the helper does not print the session ID.

The helper verifies the final redirect returns to the configured agent origin, uses the
exact `/app` path, and carries exactly one opaque session ID. Remote WebDriver endpoints
must use HTTPS; HTTP is permitted only for a loopback grid.

## Run the automated compatibility path

Start the repository's Selenium service from the repository root:

```bash
docker compose -f docker/development-easy/docker-compose.yml up --detach --wait selenium
```

Install the already-declared agent development dependencies and run the helper:

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python bruno/dev_mint.py
```

The helper prompts for the synthetic-demo password without echo. For non-interactive
grader automation, supply it through the process environment:

```bash
OE_ADMIN_PASS='<synthetic-demo password>' python bruno/dev_mint.py
```

Do not put the password on the command line, in a Bruno file, or in git. The supported
non-secret overrides are:

```text
AGENT_BASE_URL   deployed agent root; defaults to the current Railway demo
OPENEMR_BASE_URL expected browser origin; defaults to the current synthetic demo
SELENIUM_URL     Remote WebDriver; defaults to http://localhost:4444/wd/hub
OE_USERNAME      synthetic-demo username; defaults to admin
```

Equivalent command flags are available via `python bruno/dev_mint.py --help`, including
`--openemr-base-url`, `--patient-index`, and `--timeout`. Remote agent, OpenEMR, and
WebDriver URLs must use HTTPS; HTTP is accepted only for loopback development. The output
path is intentionally fixed to the gitignored `environments/Runtime.bru`. Manual mode is
Week 2-only; the default Week 1 behavior remains the automated compatibility flow.

Then run the collection:

```bash
cd bruno
npx --yes @usebruno/cli@3.5.2 run --env Runtime --bail
```

## Expected lifecycle and failures

- `co-pilot OAuth client is not enabled`: an administrator must enable the registered
  confidential client per D14 before retrying.
- `session not found` or `session expired`: rerun the helper. The current demo session
  store and delegated-token cache are in-process, so a deploy/restart invalidates them.
- Selenium connection failure: confirm `http://localhost:4444/wd/hub/status` is reachable,
  or set `SELENIUM_URL` to the active grid.
- Login or patient-selector failure: confirm the tester credentials and that only
  synthetic Synthea data is in use.

After testing, remove the short-lived local session artifact:

```bash
rm -f environments/Runtime.bru
```
