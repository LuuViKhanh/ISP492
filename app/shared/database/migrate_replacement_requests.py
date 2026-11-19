"""
migrate_replacement_requests.py — Tạo bảng replacement_requests

Idempotent. Chạy: python app/shared/database/migrate_replacement_requests.py
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

DDL = """
DO $$ BEGIN
  CREATE TYPE replacement_type_enum AS ENUM ('BATTERY','PART');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE replacement_status_enum AS ENUM ('AWAITING_SUPPLY','SUPPLIED','REJECTED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS replacement_requests (
    id               BIGSERIAL PRIMARY KEY,
    requested_by     VARCHAR NOT NULL,
    hub_id           INTEGER NOT NULL REFERENCES hubs(id),
    type             replacement_type_enum NOT NULL,
    damaged_item_id  VARCHAR,
    part_category_id BIGINT REFERENCES part_categories(id),
    quantity         INTEGER NOT NULL DEFAULT 1,
    reason           TEXT NOT NULL,
    photo_urls       JSONB,
    status           replacement_status_enum NOT NULL DEFAULT 'AWAITING_SUPPLY',
    reviewed_by      VARCHAR,
    reviewed_at      TIMESTAMPTZ,
    rejection_reason TEXT,
    supplied_item_id VARCHAR,
    supplied_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rr_requested_by ON replacement_requests(requested_by);
CREATE INDEX IF NOT EXISTS idx_rr_hub_id       ON replacement_requests(hub_id);
CREATE INDEX IF NOT EXISTS idx_rr_status       ON replacement_requests(status);
CREATE INDEX IF NOT EXISTS idx_rr_type         ON replacement_requests(type);
CREATE INDEX IF NOT EXISTS idx_rr_created_at   ON replacement_requests(created_at DESC);
"""

if __name__ == "__main__":
    print("Migrate: replacement_requests...")
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(DDL))
    print("Done!")
