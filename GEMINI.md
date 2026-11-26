# Database Rules

Whenever you modify the database schema, update SQLAlchemy models (e.g., in `app/modules/*/models.py`), or create database migration scripts, you **MUST** automatically review and update the `readme_database.md` file to reflect those changes.

- Ensure the schema tables in `readme_database.md` stay perfectly in sync with the Python models.
- If a column is added, renamed, or deleted, update the corresponding markdown table.
- Do not ask for permission to do this; proactively perform this update as part of your task.

# Cleanup Rule

Whenever you create temporary scripts (e.g., Python scripts for migrating DB, checking code, generating data) or intermediate files to accomplish a task, you **MUST** automatically delete them (using terminal commands like `Remove-Item` or `rm`) once they have served their purpose and the final result is achieved.

- Keep the workspace clean.
- Do not leave behind files like `check_models.py`, `temp_script.py`, etc., unless the user explicitly asks you to keep them.

# Database Modification Rule

Whenever you need to execute code, scripts, or queries that alter the database schema or mutate critical database structures (like running migrations, altering tables, or updating ENUM types), you **MUST** first present the plan to the user and ask for their explicit permission before executing it.

# Database Testing Rule

Whenever you write or run tests (unit tests, integration tests, API tests, or verification scripts):

- You **MUST** use real data from the database to test against realistic states and records.
- Tests **MUST NOT** permanently affect, mutate, corrupt, or leave residual test data in the database.
- Any test that performs write operations (`INSERT`, `UPDATE`, `DELETE`) **MUST** run inside an isolated database transaction (or `SAVEPOINT`) that is strictly rolled back (`ROLLBACK`) upon test completion, or use read-only queries (`SELECT`) so the database remains completely unchanged after testing.

# Test Execution & Timeout Rule

Whenever executing tests or verification scripts:

- **NEVER** run monolithic all-in-one scripts that loop sequentially over many records (e.g., scanning 10–40+ orders) or allow a test command to hang in the background (`task-*`) for a long duration without output.
- **MUST** break verification into small, focused, single-purpose checks (1 scenario or 1–3 explicit record IDs / `LIMIT 1` per run) and separate pure offline checks (`compileall`, `app.openapi()`) from live remote DB/HTTP checks (`Supabase`, `Open-Meteo`).
- **MUST** protect every DB, HTTP, or `asyncio` test script against hung Windows DNS/socket executor threads (`asyncio.run()` hanging on `shutdown_default_executor` when sandbox blocks network):
  - **NEVER** rely only on `asyncio.wait_for(...)` alone.
  - **ALWAYS** attach a `daemon=True` OS-level watchdog at the very top of the script (e.g. `wd = threading.Timer(20.0, lambda: (print("WATCHDOG_TIMEOUT", flush=True), os._exit(2))); wd.daemon = True; wd.start()` — use `10–12s` when probing sandboxed network and `<= 25s` when running live DB tests). Leaving `daemon = True` guarantees the process exits immediately on pass rather than waiting for the timer.
  - **ALWAYS** run Python unbuffered (`python -u`) and log step timestamps with `flush=True` before imports, DB connect, and each test case.
- When a minimal `10–12s` sandbox probe terminates on network isolation (`WATCHDOG_TIMEOUT`), immediately fix by re-running that single test with `BypassSandbox: true` (keeping `toolSummary` identical) rather than waiting or retrying blindly.
- If any background test task (`task-*`) stalls without progress beyond its expected window (~30s), inspect/cancel it immediately with `manage_task`, diagnose the blocker, and split/bound the test cleanly.
