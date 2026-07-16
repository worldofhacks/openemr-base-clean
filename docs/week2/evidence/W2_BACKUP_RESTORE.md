# Week 2 backup and restore drill

Targets are backup RPO <= 24 hours and measured restore RTO <= 60 minutes. Production must
retain at least seven restore points.

`python -m scripts.restore_drill --adapter module:factory --output restore-drill-results.json`
runs the required sequence against an injected isolated-environment adapter:

1. prove the target is isolated;
2. inspect the backup timestamp;
3. restore OpenEMR MySQL and its document volume;
4. restore Agent Postgres;
5. verify ordered migrations;
6. probe the encrypted delegated-credential vault;
7. require hard/soft readiness semantics;
8. require byte-exact synthetic Binary readback;
9. require duplicate reconciliation through the permanent intent authority.

The script stops on the first failed check and writes aggregate booleans plus RPO/RTO only.
It never writes commands, paths, patient/user identifiers, database rows, document bytes,
credentials, tokens, or exception bodies to the report.

Production backups and an isolated restore target are not available to this workspace.
Enabling backups, authorizing the isolated adapter, and attaching the aggregate report are
owner actions; no restore is claimed here.
