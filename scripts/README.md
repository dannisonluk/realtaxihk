# `scripts/`

Standalone tooling, grouped by **what you are doing**. Nothing here is imported
by `app/`; the only exception is `tests/test_db_backup.py`, which imports the
pure helpers out of `ops/db_backup.py` to unit-test the retention logic.

| Group | Question it answers | Rule of thumb |
|---|---|---|
| `ops/` | *"Operate a real environment."* | It changes state outside the repo — a backup, an account, a TOTP secret. Running it twice matters. |
| `verify/` | *"Is this correct?"* | It prints a pass/fail verdict or a measurement. It may boot a server, but it must not mutate durable data (probes create throwaway users/orders only). |
| `dev/` | *"Glue for the local loop."* | It starts, supervises or stops a process, or generates a build artifact. Nothing it does is meaningful in production. |

If a new script does not obviously fit, prefer the group whose *rule of thumb*
matches. A script that both measures and mutates belongs in `ops/` — the risk is
in the mutation.

## `ops/`

| Script | What it does |
|---|---|
| `db_backup.py` | Nightly `pg_dump` with retention, plus `verify` (restore into a scratch DB and compare row counts) and `list`. Falls back to `docker exec` when the host has no `pg_dump`. |
| `create_admin.py` | Create or promote an `ADMIN` row in `users` — the identity the legacy admin surface checks. |
| `create_admin_account.py` | Provision an account in `admin_accounts` for the console. **Deliberately leaves it unenrolled** (no TOTP). |
| `enrol_admin_totp.py` | Enrol that account's TOTP headlessly and print the secret. |

`create_admin_account.py` and `enrol_admin_totp.py` are two halves on purpose:
a TOTP secret written by a script is a secret nobody has proven they can
generate a valid code from, so enrolment is only persisted after a real code
verifies.

A fresh console account is `SUPPORT`. Finance and account pages need
`SUPER_ADMIN`; `enrol_admin_totp.py --super-admin` promotes it.

## `verify/`

| Script | What it does |
|---|---|
| `audit_response_models.py` | Walks `mobile/test/fixtures/manifest.json` and proves every key in every captured response survives its route's `response_model=`. |
| `live_smoke.py` | Boots uvicorn and exercises modules A/B/C/D end to end, then self-terminates. |
| `security_probe.py` | The original adversarial probe — proves each finding with a real request. |
| `security_verify.py` | Re-runs every audit finding against a live server; prints PASS/FAIL per check. |
| `prod_boot_drill.py` | Boots the real app in a subprocess for each prod-config case and asserts it either refuses or boots. |
| `verify_api.py` | One-shot smoke against an already-running server. |
| `bench_location_pipeline.py` | Measures each stage of one GPS tick separately, so scaling claims are evidence rather than a guess. |
| `probe_sequential.py` / `probe_concurrency.py` / `probe_retry.py` | Pin the sandbox's connection-stall behaviour (is it uvicorn, the proxy, or concurrency?). |

## `dev/`

| Script | What it does |
|---|---|
| `serve_and_probe.py` | Spawn detached uvicorn, wait for `/health`, exit (the server keeps running). |
| `serve_and_run_browser.py` | API + console + an arbitrary Node script, all held open in **one** process — background servers do not survive between tool calls in this sandbox. |
| `run_against_api.py` | Same idea for a backend probe instead of a Node script. |
| `api_supervisor.py` | Keeps uvicorn alive; on this machine a uvicorn process serves a bounded number of requests and then stops answering permanently. |
| `stop_server.py` | Kill the detached uvicorn on `:8000`. |
| `gen_mobile_fixtures.py` | Boot the API and capture real responses into `mobile/test/fixtures/` — the mobile wire contract. |

## Running a script

Every script is run directly, from the repo root or from anywhere:

```bash
.venv/Scripts/python scripts/verify/audit_response_models.py
```

The repo root comes from `scripts/_root.py`, which sits next to this file:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _root import REPO_ROOT  # noqa: E402
```

The two lines are load-bearing in that order. `_root.py` resolves its own
location once, so **nesting depth is no longer encoded in every script** — move
a script between groups and nothing needs editing. The `sys.path.insert` still
has to come first, because `_root` is not importable until `scripts/` is on the
path; that is the chicken-and-egg, and it now lives in one place instead of
fifteen.

`scripts/_root.py` is a module, not a package (`scripts/__init__.py` is absent),
so adding it does not change pytest collection. `tests/test_scripts_root.py`
guards both halves: it fails if any script re-derives a depth (three
`parent`/`parents[2]`/nested-`dirname` spellings are checked) or if a script
referenced by name in another script does not exist.

If a script only needs `REPO_ROOT` to feed that `sys.path.insert`, it does not
need the import at all — use the two-line bootstrap above and drop the `from
_root import REPO_ROOT`.
