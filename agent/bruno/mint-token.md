# Dev-only SMART session mint helper

F5 calls this a token-mint helper, but the safe artifact for the deployed API is an
opaque agent `session_id`. The delegated OAuth access token must remain server-side.
A direct token exchange would not populate the agent's PKCE state, patient pin, token
cache, or session store, so `/chat` could not use it (§4, §7, D12, D14).

## What the helper does

`dev_mint.py` automates the same synthetic-demo flow as the opt-in live E9 test:

1. Open the deployed agent's `/launch` endpoint in the repository Selenium grid.
2. Authenticate to OpenEMR and select a synthetic Synthea patient.
3. Approve the enabled D14 SMART client.
4. Let the agent exchange the authorization code and create a patient-pinned session.
5. Parse the callback's `{session_id, patient_id}` envelope.
6. Write only `agent_base_url` and `session_id` to `environments/Runtime.bru` with mode
   `0600`. That generated file is gitignored; the helper does not print the session ID.

## Run it

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
SELENIUM_URL     Remote WebDriver; defaults to http://localhost:4444/wd/hub
OE_USERNAME      synthetic-demo username; defaults to admin
```

Equivalent command flags are available via `python bruno/dev_mint.py --help`, including
`--patient-index` and `--timeout`. Remote agent URLs must use HTTPS; HTTP is accepted only
for loopback development. The output path is intentionally fixed to the gitignored
`environments/Runtime.bru`.

Then run the collection:

```bash
cd bruno
npx --yes @usebruno/cli@3.5.1 run --env Runtime --bail
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
