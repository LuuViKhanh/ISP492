"""
Migration: Thêm leg_destination_type column vào missions table

Mục đích: Phân biệt chuyến bay thẳng (destination) vs chuyến bay trung gian (connection)
"""

from sqlalchemy import text
from app.database.db import engine


def run_migration():
    with engine.connect() as conn:
        with conn.begin():
            print("Adding leg_destination_type column to missions table...")
            
            try:
                conn.execute(text("""
                    ALTER TABLE missions 
                    ADD COLUMN IF NOT EXISTS leg_destination_type VARCHAR(20) DEFAULT 'destination';
                """))
                print("✅ Column leg_destination_type added successfully.")
            except Exception as e:
                print(f"❌ Error: {e}")
                raise
            
            # Tạo index để tối ưu query
            try:
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_missions_leg_destination_type 
                    ON missions(leg_destination_type);
                """))
                print("✅ Index created on leg_destination_type.")
            except Exception as e:
                print(f"⚠️ Index creation warning: {e}")
            
            conn.commit()
            print("✅ Migration completed!")


if __name__ == "__main__":
    run_migration()
