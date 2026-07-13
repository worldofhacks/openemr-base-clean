[![Syntax Status](https://github.com/openemr/openemr/actions/workflows/syntax.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/syntax.yml)
[![Styling Status](https://github.com/openemr/openemr/actions/workflows/styling.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/styling.yml)
[![Testing Status](https://github.com/openemr/openemr/actions/workflows/test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/test.yml)
[![JS Unit Testing Status](https://github.com/openemr/openemr/actions/workflows/js-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/js-test.yml)
[![PHPStan](https://github.com/openemr/openemr/actions/workflows/phpstan.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/phpstan.yml)
[![Rector](https://github.com/openemr/openemr/actions/workflows/rector.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/rector.yml)
[![ShellCheck](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml)
[![Docker Compose Linting](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml)
[![Dockerfile Linting](https://github.com/openemr/openemr/actions/workflows/docker-lint-hadolint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-lint-hadolint.yml)
[![Isolated Tests](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml)
[![Inferno Certification Test](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml)
[![Composer Checks](https://github.com/openemr/openemr/actions/workflows/composer.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer.yml)
[![Composer Require Checker](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml)
[![API Docs Freshness Checks](https://github.com/openemr/openemr/actions/workflows/api-docs.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/api-docs.yml)
[![codecov](https://codecov.io/gh/openemr/openemr/graph/badge.svg?token=7Eu3U1Ozdq)](https://codecov.io/gh/openemr/openemr)

[![Backers on Open Collective](https://opencollective.com/openemr/backers/badge.svg)](#backers) [![Sponsors on Open Collective](https://opencollective.com/openemr/sponsors/badge.svg)](#sponsors)

# Clinical Co-Pilot for OpenEMR

This fork adds a read-only SMART-on-FHIR sidecar that gives a primary-care clinician a
cited pre-visit brief over synthetic Synthea charts. The model never writes to OpenEMR and
its prose is never sent directly to the physician: it produces typed claims, a deterministic
verifier checks each claim against cited FHIR fields, and a deterministic renderer flushes
only verified record content. Unsupported claims are blocked; a bounded, explicitly labeled
grounded fallback is used when synthesis cannot be verified (D2/D7/D9/D13, §2/§5/§6).

- **OpenEMR:** https://openemr-production-cc95.up.railway.app
- **Clinical Co-Pilot agent:** https://agent-production-9f62.up.railway.app
- **Binding design:** [ARCHITECTURE.md](docs/week1/ARCHITECTURE.md)
- **Security/data-quality audit:** [AUDIT.md](docs/week1/AUDIT.md)
- **Target physician and UC1–UC4:** [USERS.md](USERS.md)
- **Deployment and rollback:** [DEPLOYMENT.md](DEPLOYMENT.md)
- **Eval results:** [agent/evals/results.json](agent/evals/results.json)

```text
OpenEMR chart --SMART launch + delegated read scopes--> Python sidecar
                                                        | six FHIR reads
                                                        v
                                               typed EvidencePacket
                                                        |
                                              Sonnet typed claims
                                                        |
                                      deterministic verify-then-flush
                                                        v
                                           cited practitioner brief
```

All project-specific application code lives under `agent/`; the inherited PHP EHR remains
the system of record. The demo is synthetic-data-only and intentionally has no write tools,
OpenEMR database credentials, diagnosis/prescribing behavior, or chart-mutation path.

## Agent setup

Python 3.12 is required. Variable names are documented in
[agent/.env.example](agent/.env.example); put real development values only in the gitignored
`agent/.env`.

```bash
cd agent
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m evals.runner
uvicorn app.main:app --reload --port 8000
```

`GET /health` is process liveness. `GET /ready` performs real FHIR, Anthropic, Postgres,
and Langfuse probes. A patient-specific brief requires a SMART launch from OpenEMR; the
standalone synthetic-demo fallback begins at the agent's `/launch` route. The runnable
authenticated grader flow is documented in [agent/bruno/README.md](agent/bruno/README.md).

## OpenEMR foundation

[OpenEMR](https://open-emr.org) is a Free and Open Source electronic health records and medical practice management application. It features fully integrated electronic health records, practice management, scheduling, electronic billing, internationalization, free support, a vibrant community, and a whole lot more. It runs on Windows, Linux, Mac OS X, and many other platforms.

### Setup (this fork)

**Live deployment:** https://openemr-production-cc95.up.railway.app

This fork is deployed publicly on Railway and runs locally with Docker Compose plus
~25 Synthea-generated sample patients. Full reproducible instructions — local setup,
Railway deployment architecture, environment variables, sample-data loading,
security baseline, and rollback — are in **[DEPLOYMENT.md](DEPLOYMENT.md)**.

Quick local start (Docker Desktop required):

```bash
cd docker/development-easy
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --wait
# app: http://localhost:8300  (login admin/pass)
# sample patients: openemr-cmd import-random-patients 25
```

Environment variable names used by the deployment are catalogued in
[.env.example](.env.example) (never commit real values).

### Contributing

OpenEMR is a leader in healthcare open source software and comprises a large and diverse community of software developers, medical providers and educators with a very healthy mix of both volunteers and professionals. [Join us and learn how to start contributing today!](https://open-emr.org/wiki/index.php/FAQ#How_do_I_begin_to_volunteer_for_the_OpenEMR_project.3F)

> Already comfortable with git? Check out [CONTRIBUTING.md](CONTRIBUTING.md) for quick setup instructions and requirements for contributing to OpenEMR by resolving a bug or adding an awesome feature 😊.

### Support

Community and Professional support can be found [here](https://open-emr.org/wiki/index.php/OpenEMR_Support_Guide).

Extensive documentation and forums can be found on the [OpenEMR website](https://open-emr.org) that can help you to become more familiar about the project 📖.

### Reporting Issues and Bugs

Report these on the [Issue Tracker](https://github.com/openemr/openemr/issues). If you are unsure if it is an issue/bug, then always feel free to use the [Forum](https://community.open-emr.org/) and [Chat](https://www.open-emr.org/chat/) to discuss about the issue 🪲.

### Reporting Security Vulnerabilities

Check out [SECURITY.md](.github/SECURITY.md)

### API

Check out [API_README.md](API_README.md)

### Docker

Check out [DOCKER_README.md](DOCKER_README.md)

### FHIR

Check out [FHIR_README.md](FHIR_README.md)

### For Developers

If using OpenEMR directly from the code repository, then the following commands will build OpenEMR (Node.js version 24.* is required) :

```shell
composer install --no-dev
npm install
npm run build
composer dump-autoload -o
```

### Contributors

This project exists thanks to all the people who have contributed. [[Contribute]](CONTRIBUTING.md).
<a href="https://github.com/openemr/openemr/graphs/contributors"><img src="https://opencollective.com/openemr/contributors.svg?width=890" /></a>


### Sponsors

Thanks to our [ONC Certification Major Sponsors](https://www.open-emr.org/wiki/index.php/OpenEMR_Certification_Stage_III_Meaningful_Use#Major_sponsors)!


### License

[GNU GPL](LICENSE)
