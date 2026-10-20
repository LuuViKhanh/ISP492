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
    print("Bat dau migrate Aging Priority schema...")
    with engine.connect() as conn:
        try:
            # Bước 1: ADD VALUE phải được commit trước trong transaction riêng
            conn.execute(text("ALTER TYPE delivery_mode_enum ADD VALUE IF NOT EXISTS 'NORMAL';"))
            conn.commit()
            print("OK: Them NORMAL vao delivery_mode_enum.")
        except Exception as e:
            conn.rollback()
            print(f"Loi buoc 1 (co the da ton tai): {e}")

    with engine.connect() as conn:
        try:
            # Bước 2: Thêm cột vào orders
            conn.execute(text("""
                ALTER TABLE orders
                ADD COLUMN IF NOT EXISTS is_aging_priority BOOLEAN NOT NULL DEFAULT FALSE;
            """))
            print("OK: Them cot is_aging_priority.")

            conn.execute(text("""
                ALTER TABLE orders
                ADD COLUMN IF NOT EXISTS aging_deadline TIMESTAMP;
            """))
            print("OK: Them cot aging_deadline.")

            # Index không dùng WHERE để tránh lỗi PostgreSQL enum
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_orders_aging
                ON orders (delivery_mode, status, is_aging_priority, aging_deadline);
            """))
            print("OK: Tao index idx_orders_aging.")

            conn.commit()
            print("Migration Aging Priority hoan thanh!")

        except Exception as e:
            conn.rollback()
            print(f"Loi buoc 2: {e}")
            raise


if __name__ == "__main__":
    migrate()
