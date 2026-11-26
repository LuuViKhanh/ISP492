"""Migration: Tạo bảng saved_addresses cho Address Book."""
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
                CREATE TABLE IF NOT EXISTS saved_addresses (
                    id              BIGSERIAL PRIMARY KEY,
                    customer_id     VARCHAR(50)  NOT NULL,
                    label           VARCHAR(100) NOT NULL,
                    recipient_name  VARCHAR(100) NOT NULL,
                    recipient_phone VARCHAR(20)  NOT NULL,
                    recipient_email VARCHAR(255),
                    hub_id          BIGINT,
                    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMP NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_saved_addresses_customer ON saved_addresses (customer_id)"))
    print("OK — Table saved_addresses created.")

if __name__ == "__main__":
    run()
