"""
migrate_sender_email.py — Add sender_email column to orders table
Idempotent. Run: python app/shared/database/migrate_sender_email.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(0, "C:/Users/ext.han.bui/AppData/Roaming/Python/Python314/site-packages")
from dotenv import load_dotenv; load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))
from sqlalchemy import create_engine, text; import os as _os

_raw = _os.getenv("DATABASE_URL","").replace("+asyncpg","")
if "postgresql" in _raw and "+psycopg2" not in _raw:
    _raw = _raw.replace("postgresql://","postgresql+psycopg2://")
engine = create_engine(_raw)

if __name__ == "__main__":
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS sender_email VARCHAR(255)"
            ))
            # Verify
            exists = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='orders' AND column_name='sender_email'"
            )).scalar()
            print(f"sender_email column: {'OK' if exists else 'MISSING'}")
    print("Migration done.")
