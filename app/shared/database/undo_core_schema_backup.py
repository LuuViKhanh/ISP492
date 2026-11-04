"""
Script khôi phục (Undo / Restore) toàn bộ cấu trúc và dữ liệu Database
từ file backup_full_db_snapshot.json (được tạo trước khi thực hiện kế hoạch Core Schema).

Cách sử dụng:
  1. Xem trước kế hoạch khôi phục (Dry Run - mặc định):
     python app/shared/database/undo_core_schema_backup.py

  2. Thực thi khôi phục thật sự:
     python app/shared/database/undo_core_schema_backup.py --execute
"""
import os
import sys
import json
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "")
SYNC_DB_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
BACKUP_FILE = os.path.join(BASE_DIR, "backup_full_db_snapshot.json")


def _pg_col_type(col: dict) -> str:
    dtype = col["data_type"]
    udt = col["udt_name"]
    max_len = col["character_maximum_length"]
    if dtype == "USER-DEFINED":
        return f'public."{udt}"'
    if dtype == "character varying":
        return f"VARCHAR({max_len})" if max_len else "VARCHAR"
    if dtype == "timestamp with time zone":
        return "TIMESTAMPTZ"
    if dtype == "timestamp without time zone":
        return "TIMESTAMP"
    if dtype == "double precision":
        return "DOUBLE PRECISION"
    if dtype == "bigint":
        return "BIGINT"
    if dtype == "integer":
        return "INTEGER"
    if dtype == "boolean":
        return "BOOLEAN"
    if dtype == "jsonb":
        return "JSONB"
    if dtype == "json":
        return "JSON"
    if dtype == "text":
        return "TEXT"
    return udt.upper()


def main():
    execute = "--execute" in sys.argv
    if not os.path.exists(BACKUP_FILE):
        print(f"[ERROR] Backup file not found: {BACKUP_FILE}")
        sys.exit(1)

    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
        backup = json.load(f)

    print("=" * 72)
    print(f"BACKUP SNAPSHOT: {backup.get('backup_created_at')}")
    print(f"Total Enums    : {len(backup.get('enums', {}))}")
    print(f"Total Tables   : {len(backup.get('tables', {}))}")
    total_rows = sum(t["row_count"] for t in backup.get("tables", {}).values())
    print(f"Total Rows     : {total_rows}")
    print(f"Mode           : {'EXECUTE (RESTORE LIVE DB)' if execute else 'DRY-RUN (SAFE PREVIEW)'}")
    print("=" * 72)

    if not execute:
        for tname, tdata in backup["tables"].items():
            print(f"  - Table '{tname}': {len(tdata['columns'])} columns, {tdata['row_count']} rows backed up")
        print("\nRun with --execute to restore the database to this snapshot.")
        return

    conn = psycopg2.connect(SYNC_DB_URL, sslmode="require")
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 1. If 'accounts' exists and 'users' does not, rename 'accounts' back to 'users' first
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name IN ('accounts', 'users');
        """)
        existing_auth_tables = {r[0] for r in cur.fetchall()}
        if "accounts" in existing_auth_tables and "users" not in existing_auth_tables:
            print("Renaming table 'accounts' -> 'users'...")
            cur.execute('ALTER TABLE public."accounts" RENAME TO "users";')

        # 2. Ensure all ENUM types and values exist
        for enum_name, labels in backup.get("enums", {}).items():
            cur.execute(
                "SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' AND t.typname = %s;",
                (enum_name,),
            )
            if not cur.fetchone():
                labels_sql = ", ".join(f"'{lbl}'" for lbl in labels)
                cur.execute(f'CREATE TYPE public."{enum_name}" AS ENUM ({labels_sql});')

        # 3. Recreate any missing tables and missing columns
        for tname, tdata in backup["tables"].items():
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s;",
                (tname,),
            )
            exists = cur.fetchone() is not None
            cols = tdata["columns"]
            if not exists:
                col_defs = []
                for c in cols:
                    ctype = _pg_col_type(c)
                    nullable = "" if c["is_nullable"] == "YES" else " NOT NULL"
                    default_str = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
                    col_defs.append(f'"{c["column_name"]}" {ctype}{default_str}{nullable}')
                create_sql = f'CREATE TABLE public."{tname}" ({", ".join(col_defs)});'
                print(f"Creating missing table '{tname}'...")
                cur.execute(create_sql)
            else:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s;",
                    (tname,),
                )
                existing_cols = {r[0] for r in cur.fetchall()}
                for c in cols:
                    if c["column_name"] not in existing_cols:
                        ctype = _pg_col_type(c)
                        print(f"Restoring missing column '{tname}.{c['column_name']}' ({ctype})...")
                        cur.execute(f'ALTER TABLE public."{tname}" ADD COLUMN "{c["column_name"]}" {ctype};')

                # Drop any newly added columns not in original snapshot
                snapshot_cols = {c["column_name"] for c in cols}
                for ecol in existing_cols - snapshot_cols:
                    print(f"Dropping post-backup column '{tname}.{ecol}'...")
                    cur.execute(f'ALTER TABLE public."{tname}" DROP COLUMN IF EXISTS "{ecol}" CASCADE;')

        # 4. Restore row data for all tables
        for tname, tdata in backup["tables"].items():
            rows = tdata["rows"]
            cur.execute(f'TRUNCATE TABLE public."{tname}" RESTART IDENTITY CASCADE;')
            if not rows:
                continue
            col_names = [c["column_name"] for c in tdata["columns"]]
            cols_quoted = ", ".join(f'"{c}"' for c in col_names)
            placeholders = ", ".join(["%s"] * len(col_names))
            insert_sql = f'INSERT INTO public."{tname}" ({cols_quoted}) VALUES ({placeholders});'
            for r in rows:
                vals = [
                    Json(r[c]) if isinstance(r.get(c), (dict, list)) else r.get(c)
                    for c in col_names
                ]
                cur.execute(insert_sql, vals)
            print(f"Restored {len(rows)} rows into '{tname}'.")

        conn.commit()
        print("\n[SUCCESS] Database restored to backup snapshot successfully!")
    except Exception as exc:
        conn.rollback()
        print(f"\n[ERROR] Restore failed, transaction rolled back: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
