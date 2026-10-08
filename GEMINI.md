# Database Rules

Whenever you modify the database schema, update SQLAlchemy models (e.g., in pp/modules/*/models.py), or create database migration scripts, you **MUST** automatically review and update the eadme_database.md file to reflect those changes.

- Ensure the schema tables in eadme_database.md stay perfectly in sync with the Python models.
- If a column is added, renamed, or deleted, update the corresponding markdown table.
- Do not ask for permission to do this; proactively perform this update as part of your task.

# Cleanup Rule

Whenever you create temporary scripts (e.g., Python scripts for migrating DB, checking code, generating data) or intermediate files to accomplish a task, you **MUST** automatically delete them (using terminal commands like Remove-Item or m) once they have served their purpose and the final result is achieved. 

- Keep the workspace clean.
- Do not leave behind files like check_models.py, 	emp_script.py, etc., unless the user explicitly asks you to keep them.

# Database Modification Rule

Whenever you need to execute code, scripts, or queries that alter the database schema or mutate critical database structures (like running migrations, altering tables, or updating ENUM types), you **MUST** first present the plan to the user and ask for their explicit permission before executing it.
