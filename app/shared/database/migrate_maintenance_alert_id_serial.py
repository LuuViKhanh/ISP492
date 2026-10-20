"""
migrate_maintenance_alert_id_serial.py
────────────────────────────────────────
Fix: maintenance_alerts.id không có SERIAL/sequence → null value violation khi INSERT.

Chạy 1 lần trên production DB:
    python -m app.shared.database.migrate_maintenance_alert_id_serial
"""
import asyncio
from sqlalchemy import text
from app.database.db import AsyncSessionLocal


async def migrate():
    async with AsyncSessionLocal() as db:
        # Tạo sequence nếu chưa có và gán làm default cho cột id
        await db.execute(text("""
            DO $$
            BEGIN
                -- Tạo sequence nếu chưa tồn tại
                IF NOT EXISTS (
                    SELECT 1 FROM pg_sequences
                    WHERE schemaname = 'public'
                    AND sequencename = 'maintenance_alerts_id_seq'
                ) THEN
                    CREATE SEQUENCE maintenance_alerts_id_seq
                        START WITH 1
                        INCREMENT BY 1
                        NO MINVALUE
                        NO MAXVALUE
                        CACHE 1;
                END IF;

                -- Set sequence value bắt đầu từ max id hiện tại + 1
                PERFORM setval(
                    'maintenance_alerts_id_seq',
                    COALESCE((SELECT MAX(id) FROM maintenance_alerts), 0) + 1,
                    false
                );

                -- Gán sequence làm default cho cột id
                ALTER TABLE maintenance_alerts
                    ALTER COLUMN id SET DEFAULT nextval('maintenance_alerts_id_seq');

            END $$;
        """))
        await db.commit()
        print("✅ maintenance_alerts.id đã được gán SERIAL sequence thành công.")


if __name__ == "__main__":
    asyncio.run(migrate())
