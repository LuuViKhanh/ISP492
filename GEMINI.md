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
