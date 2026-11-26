"""
Migration: Tạo bảng delivery_ratings cho tính năng Customer Rating.
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
            print("[1] Tạo bảng delivery_ratings...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS delivery_ratings (
                    id          BIGSERIAL PRIMARY KEY,
                    order_id    VARCHAR(50)  NOT NULL UNIQUE,
                    customer_id VARCHAR(50)  NOT NULL,
                    rating      INTEGER      NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    comment     VARCHAR(1000),
                    created_at  TIMESTAMP    NOT NULL
                )
            """))

            print("[2] Tạo indexes...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_delivery_ratings_order
                ON delivery_ratings (order_id)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_delivery_ratings_customer
                ON delivery_ratings (customer_id)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_delivery_ratings_rating
                ON delivery_ratings (rating)
            """))

    print("OK — Tao bang delivery_ratings thanh cong.")


if __name__ == "__main__":
    run()
