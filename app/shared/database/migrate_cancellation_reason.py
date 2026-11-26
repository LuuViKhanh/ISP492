"""Migration: Thêm cancellation_reason vào bảng orders."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import create_engine, text

SYNC_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql+psycopg2")
engine = create_engine(SYNC_URL, pool_pre_ping=True, connect_args={"sslmode": "require"})

def run():
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS cancellation_reason VARCHAR(500) DEFAULT NULL"))
    print("OK — Added cancellation_reason to orders.")

if __name__ == "__main__":
    run()
