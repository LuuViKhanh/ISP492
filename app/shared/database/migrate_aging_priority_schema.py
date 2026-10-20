"""
Migration: Thêm NORMAL delivery mode và Aging Priority cho Orders.

Chạy một lần:
  python app/shared/database/migrate_aging_priority_schema.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from sqlalchemy import text
from app.database.db import engine


def migrate():
    print("🚀 Bắt đầu migrate Aging Priority schema...")
    with engine.connect() as conn:
        try:
            # 1. Thêm giá trị NORMAL vào enum delivery_mode_enum
            conn.execute(text("ALTER TYPE delivery_mode_enum ADD VALUE IF NOT EXISTS 'NORMAL';"))
            print("✅ Thêm NORMAL vào delivery_mode_enum.")

            # 2. Thêm cột is_aging_priority
            conn.execute(text("""
                ALTER TABLE orders
                ADD COLUMN IF NOT EXISTS is_aging_priority BOOLEAN NOT NULL DEFAULT FALSE;
            """))
            print("✅ Thêm cột is_aging_priority.")

            # 3. Thêm cột aging_deadline
            conn.execute(text("""
                ALTER TABLE orders
                ADD COLUMN IF NOT EXISTS aging_deadline TIMESTAMP;
            """))
            print("✅ Thêm cột aging_deadline.")

            # 4. Index để worker quét nhanh
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_orders_aging
                ON orders (delivery_mode, status, is_aging_priority, aging_deadline)
                WHERE delivery_mode = 'NORMAL';
            """))

            conn.commit()
            print("✅ Migration Aging Priority hoàn thành!")

        except Exception as e:
            conn.rollback()
            print(f"❌ Lỗi: {e}")
            raise


if __name__ == "__main__":
    migrate()
