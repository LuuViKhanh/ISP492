"""
Migration: Thêm total_flight_hours và last_maintenance_at vào bảng drones.
Dùng cho tính năng Preventive Maintenance Scheduler.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")
SYNC_URL = DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2")
engine = create_engine(SYNC_URL, pool_pre_ping=True, connect_args={"sslmode": "require"})


def run():
    with engine.connect() as conn:
        with conn.begin():
            print("[1] Thêm cột total_flight_hours...")
            conn.execute(text("""
                ALTER TABLE drones
                ADD COLUMN IF NOT EXISTS total_flight_hours FLOAT NOT NULL DEFAULT 0.0
            """))

            print("[2] Thêm cột last_maintenance_at...")
            conn.execute(text("""
                ALTER TABLE drones
                ADD COLUMN IF NOT EXISTS last_maintenance_at TIMESTAMP DEFAULT NULL
            """))

            print("[3] Tính sơ bộ total_flight_hours từ missions đã hoàn thành...")
            # Mỗi mission có departed_at và arrived_at -> tính giờ bay thực tế
            conn.execute(text("""
                UPDATE drones d
                SET total_flight_hours = COALESCE((
                    SELECT SUM(
                        EXTRACT(EPOCH FROM (
                            COALESCE(m.arrived_at, m.end_time) - m.departed_at
                        )) / 3600.0
                    )
                    FROM missions m
                    WHERE m.drone_id = d.id
                      AND m.departed_at IS NOT NULL
                      AND COALESCE(m.arrived_at, m.end_time) IS NOT NULL
                      AND COALESCE(m.arrived_at, m.end_time) > m.departed_at
                      AND m.status::text IN ('Completed', 'Arrival completed', 'Mission completed',
                                            'Flying', 'Incident return', 'Failed')
                ), 0.0)
            """))

    print("OK — Migration drone flight hours complete.")


if __name__ == "__main__":
    run()
