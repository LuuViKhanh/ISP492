"""
Migration: Thêm acknowledged_by, acknowledged_at vào maintenance_alerts
và thêm giá trị 'Acknowledged' cho cột status.

Chạy một lần:
  python app/shared/database/migrate_alert_acknowledge.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from sqlalchemy import text
from app.database.db import engine


def migrate():
    print("🚀 Migrate Alert Acknowledge schema...")
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                ALTER TABLE maintenance_alerts
                ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(50),
                ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMP;
            """))
            print("✅ Thêm acknowledged_by, acknowledged_at vào maintenance_alerts.")

            conn.commit()
            print("✅ Migration hoàn thành!")
        except Exception as e:
            conn.rollback()
            print(f"❌ Lỗi: {e}")
            raise


if __name__ == "__main__":
    migrate()
