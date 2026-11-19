"""
migrate_logs_schema.py — Tách audit_logs thành activity_logs + system_logs

Idempotent — chạy lại an toàn.
Chạy: python app/shared/database/migrate_logs_schema.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(0, "C:/Users/ext.han.bui/AppData/Roaming/Python/Python314/site-packages")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))

from sqlalchemy import create_engine, text
import os as _os

_raw_url = _os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
if "postgresql" in _raw_url and "+psycopg2" not in _raw_url:
    _raw_url = _raw_url.replace("postgresql://", "postgresql+psycopg2://")
engine = create_engine(_raw_url, echo=False)

DDL = """
-- ── activity_logs ────────────────────────────────────────────────
-- Hành động do user khởi tạo: LOGIN, REGISTER, CREATE_ORDER, ...
CREATE TABLE IF NOT EXISTS activity_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         VARCHAR,
    user_email      VARCHAR,
    role            VARCHAR(50),
    action          VARCHAR(100) NOT NULL,
    resource_type   VARCHAR(100),
    resource_id     VARCHAR,
    description     TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'success',
    details_json    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_activity_user_id    ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_action     ON activity_logs(action);
CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_role       ON activity_logs(role);

-- ── system_logs ───────────────────────────────────────────────────
-- Sự kiện tự động: workers, cron, errors nền
CREATE TABLE IF NOT EXISTS system_logs (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(100) NOT NULL,
    level           VARCHAR(20)  NOT NULL DEFAULT 'INFO',
    event           VARCHAR(100) NOT NULL,
    message         TEXT NOT NULL,
    details_json    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_system_source     ON system_logs(source);
CREATE INDEX IF NOT EXISTS idx_system_level      ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_system_created_at ON system_logs(created_at DESC);

-- ── Migrate dữ liệu cũ từ audit_logs → activity_logs ─────────────
INSERT INTO activity_logs (user_id, action, resource_type, details_json, created_at)
SELECT user_id, action, resource_table, details_json, created_at
FROM audit_logs
WHERE NOT EXISTS (
    SELECT 1 FROM activity_logs al
    WHERE al.user_id = audit_logs.user_id
      AND al.action  = audit_logs.action
      AND al.created_at = audit_logs.created_at
)
ON CONFLICT DO NOTHING;
"""

if __name__ == "__main__":
    print("=" * 50)
    print("  Migrate: activity_logs + system_logs")
    print("=" * 50)
    with engine.connect() as conn:
        with conn.begin():
            print("[1] Tạo bảng activity_logs...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id              BIGSERIAL PRIMARY KEY,
                    user_id         VARCHAR,
                    user_email      VARCHAR,
                    role            VARCHAR(50),
                    action          VARCHAR(100) NOT NULL,
                    resource_type   VARCHAR(100),
                    resource_id     VARCHAR,
                    description     TEXT,
                    status          VARCHAR(20) NOT NULL DEFAULT 'success',
                    details_json    JSONB,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_user_id ON activity_logs(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_logs(action)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity_logs(created_at DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_role ON activity_logs(role)"))
            print("    OK")

            print("[2] Tạo bảng system_logs...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS system_logs (
                    id              BIGSERIAL PRIMARY KEY,
                    source          VARCHAR(100) NOT NULL,
                    level           VARCHAR(20)  NOT NULL DEFAULT 'INFO',
                    event           VARCHAR(100) NOT NULL,
                    message         TEXT NOT NULL,
                    details_json    JSONB,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_system_source ON system_logs(source)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_system_level ON system_logs(level)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_system_created_at ON system_logs(created_at DESC)"))
            print("    OK")

            print("[3] Migrate dữ liệu từ audit_logs → activity_logs...")
            result = conn.execute(text("""
                INSERT INTO activity_logs (user_id, action, resource_type, details_json, created_at)
                SELECT user_id, action, resource_table, details_json, created_at
                FROM audit_logs
                ON CONFLICT DO NOTHING
            """))
            print(f"    Migrated {result.rowcount} rows")

    print("\nMigration hoàn tất!")
    print("  Tables: activity_logs, system_logs")
    print("  Dữ liệu cũ từ audit_logs đã được copy sang activity_logs")
