"""Migration: Tạo bảng drone_daily_stats."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import create_engine, text

SYNC_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql+psycopg2")
engine = create_engine(SYNC_URL, pool_pre_ping=True, connect_args={"sslmode": "require"})

def run():
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS drone_daily_stats (
                    id                BIGSERIAL PRIMARY KEY,
                    drone_id          VARCHAR(50) NOT NULL,
                    stat_date         TIMESTAMP   NOT NULL,
                    mission_count     INTEGER NOT NULL DEFAULT 0,
                    completed_count   INTEGER NOT NULL DEFAULT 0,
                    incident_count    INTEGER NOT NULL DEFAULT 0,
                    total_distance_km FLOAT   NOT NULL DEFAULT 0.0,
                    total_flight_min  FLOAT   NOT NULL DEFAULT 0.0,
                    total_energy_wh   FLOAT   NOT NULL DEFAULT 0.0,
                    created_at        TIMESTAMP NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_drone_daily_drone_date ON drone_daily_stats (drone_id, stat_date)"))
    print("OK — Table drone_daily_stats created.")

if __name__ == "__main__":
    run()
