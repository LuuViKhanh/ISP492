"""
Migration tổng hợp — đồng bộ DB với readme_database.md (Single Source of Truth).

Gộp TẤT CẢ thay đổi từ các migration riêng lẻ đã được thực hiện trong dự án,
bao gồm các cột/bảng chưa có trong README cũ:

  1. missions     — thêm Spec VI columns (route_id, predicted_*, risk_level, ...)
  2. maintenance_alerts — thêm acknowledged_by / acknowledged_at
  3. orders       — thêm is_aging_priority / aging_deadline / receiver_email
  4. users        — thêm username
  5. knowledge_chunks — bảng MỚI cho RAG chatbot (pgvector)

Thiết kế idempotent: dùng IF NOT EXISTS và ADD COLUMN IF NOT EXISTS
→ an toàn khi chạy nhiều lần.

Chạy 1 lần sau khi pull code mới:
    python app/shared/database/migrate_readme_sync.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from sqlalchemy import text
from app.database.db import engine


def run():
    print("🚀  Bắt đầu migrate_readme_sync …")

    with engine.connect() as conn:
        try:
            # ════════════════════════════════════════════════════════════════
            # 1. missions — Spec VI columns
            # ════════════════════════════════════════════════════════════════
            print("1. missions — thêm các cột Spec VI …")

            spec_vi_cols = [
                ("order_code",                  "VARCHAR(50)"),
                ("mission_code",                "VARCHAR(50)"),
                ("route_id",                    "VARCHAR(50)"),
                ("planned_start_at",            "TIMESTAMP"),
                ("package_receive_deadline_at", "TIMESTAMP"),
                ("estimated_arrival_at",        "TIMESTAMP"),
                ("actual_started_at",           "TIMESTAMP"),
                ("actual_completed_at",         "TIMESTAMP"),
                ("predicted_duration_min",      "INTEGER"),
                ("estimated_energy_wh",         "DECIMAL(6,2)"),
                ("battery_consumption_pct",     "INTEGER"),
                ("predicted_remaining_battery_pct", "INTEGER"),
                ("confidence_pct",              "INTEGER"),
                ("risk_level",                  "VARCHAR(50)"),
                ("created_by",                  "VARCHAR(50)"),
                ("created_at",                  "TIMESTAMP DEFAULT NOW()"),
                ("updated_at",                  "TIMESTAMP DEFAULT NOW()"),
                ("cancel_reason",               "TEXT"),
                ("failure_reason",              "TEXT"),
            ]
            for col, dtype in spec_vi_cols:
                conn.execute(text(
                    f"ALTER TABLE missions ADD COLUMN IF NOT EXISTS {col} {dtype};"
                ))
            print("   ✅ missions OK")

            # ════════════════════════════════════════════════════════════════
            # 2. maintenance_alerts — Acknowledge columns
            # ════════════════════════════════════════════════════════════════
            print("2. maintenance_alerts — acknowledged_by / acknowledged_at …")
            conn.execute(text(
                "ALTER TABLE maintenance_alerts "
                "ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(50), "
                "ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMP;"
            ))
            print("   ✅ maintenance_alerts OK")

            # ════════════════════════════════════════════════════════════════
            # 3. orders — Aging Priority + receiver_email
            # ════════════════════════════════════════════════════════════════
            print("3. orders — is_aging_priority / aging_deadline / receiver_email …")

            # NORMAL enum value (phải commit riêng trước ALTER TYPE)
            conn.execute(text(
                "ALTER TYPE delivery_mode_enum ADD VALUE IF NOT EXISTS 'NORMAL';"
            ))
            conn.commit()  # ADD VALUE requires its own transaction in PG

            with engine.connect() as conn2:
                conn2.execute(text(
                    "ALTER TABLE orders "
                    "ADD COLUMN IF NOT EXISTS is_aging_priority BOOLEAN NOT NULL DEFAULT FALSE, "
                    "ADD COLUMN IF NOT EXISTS aging_deadline TIMESTAMP, "
                    "ADD COLUMN IF NOT EXISTS receiver_email VARCHAR(255);"
                ))
                print("   ✅ orders OK")

                # ════════════════════════════════════════════════════════════
                # 4. users — username column
                # ════════════════════════════════════════════════════════════
                print("4. users — username …")
                conn2.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR;"
                ))
                print("   ✅ users OK")

                # ════════════════════════════════════════════════════════════
                # 5. knowledge_chunks — bảng RAG mới
                # ════════════════════════════════════════════════════════════
                print("5. knowledge_chunks — bảng RAG (pgvector) …")
                conn2.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                conn2.execute(text("""
                    CREATE TABLE IF NOT EXISTS knowledge_chunks (
                        id          BIGSERIAL PRIMARY KEY,
                        role        VARCHAR(50)  NOT NULL DEFAULT 'all',
                        title       VARCHAR(500),
                        content     TEXT         NOT NULL,
                        category    VARCHAR(100),
                        embedding   vector(768),
                        created_at  TIMESTAMP    DEFAULT NOW()
                    );
                """))
                conn2.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
                    ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 10);
                """))
                conn2.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_role
                    ON knowledge_chunks (role);
                """))
                print("   ✅ knowledge_chunks OK")

                conn2.commit()

        except Exception as e:
            print(f"❌ Lỗi: {e}")
            raise

    print("\n✅  migrate_readme_sync hoàn thành!")
    print("   Tất cả thay đổi đã được đồng bộ với readme_database.md.")


if __name__ == "__main__":
    run()
