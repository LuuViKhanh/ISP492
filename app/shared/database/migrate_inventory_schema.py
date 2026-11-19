"""
migrate_inventory_schema.py

Tạo các bảng mới cho module Inventory:
  - part_categories
  - hub_inventories
  - inventory_transactions
  - parts_requests

Chạy: python app/shared/database/migrate_inventory_schema.py

Idempotent: dùng CREATE TABLE IF NOT EXISTS.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(0, "C:/Users/ext.han.bui/AppData/Roaming/Python/Python314/site-packages")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))

from sqlalchemy import create_engine, text
import os as _os

_raw_url = _os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
if "postgresql" in _raw_url and "+psycopg2" not in _raw_url:
    _raw_url = _raw_url.replace("postgresql://", "postgresql+psycopg2://")
engine = create_engine(_raw_url, echo=False)

DDL = """
-- ── Enums ────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE part_category_type_enum AS ENUM (
    'Motor', 'Propeller', 'ESC', 'FlightController', 'Cable', 'Frame', 'Sensor', 'Other'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE parts_request_status_enum AS ENUM (
    'Pending', 'AutoApproved', 'Approved', 'Fulfilled', 'Rejected', 'WaitingStock'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE txn_reason_enum AS ENUM (
    'Receive', 'RepairUse', 'TransferIn', 'TransferOut', 'Adjust', 'Discard'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── part_categories ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS part_categories (
    id                       BIGSERIAL PRIMARY KEY,
    name                     VARCHAR(100) NOT NULL UNIQUE,
    category_type            part_category_type_enum NOT NULL,
    compatible_drone_models  JSONB,
    unit_cost                FLOAT,
    description              TEXT,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    created_at               TIMESTAMPTZ
);

-- ── hub_inventories ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hub_inventories (
    id                   BIGSERIAL PRIMARY KEY,
    hub_id               INTEGER NOT NULL REFERENCES hubs(id),
    part_category_id     BIGINT  NOT NULL REFERENCES part_categories(id),
    quantity             INTEGER NOT NULL DEFAULT 0,
    min_stock_threshold  INTEGER NOT NULL DEFAULT 2,
    updated_at           TIMESTAMPTZ,
    CONSTRAINT uq_hub_part UNIQUE (hub_id, part_category_id)
);

-- ── inventory_transactions ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory_transactions (
    id               BIGSERIAL PRIMARY KEY,
    hub_id           INTEGER NOT NULL,
    part_category_id BIGINT  NOT NULL,
    quantity_delta   INTEGER NOT NULL,
    reason           txn_reason_enum NOT NULL,
    performed_by     VARCHAR,
    reference_id     VARCHAR,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL
);

-- ── parts_requests ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parts_requests (
    id               BIGSERIAL PRIMARY KEY,
    requested_by     VARCHAR NOT NULL,
    hub_id           INTEGER NOT NULL,
    drone_id         VARCHAR(50),
    part_category_id BIGINT  NOT NULL,
    quantity_needed  INTEGER NOT NULL DEFAULT 1,
    description      TEXT NOT NULL,
    photo_urls       JSONB,
    status           parts_request_status_enum NOT NULL DEFAULT 'Pending',
    reviewed_by      VARCHAR,
    admin_note       TEXT,
    reviewed_at      TIMESTAMPTZ,
    actual_cost      FLOAT,
    created_at       TIMESTAMPTZ NOT NULL,
    fulfilled_at     TIMESTAMPTZ
);

-- ── Indexes ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_inventory_txn_hub ON inventory_transactions(hub_id);
CREATE INDEX IF NOT EXISTS idx_inventory_txn_created ON inventory_transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_parts_req_status ON parts_requests(status);
CREATE INDEX IF NOT EXISTS idx_parts_req_hub ON parts_requests(hub_id);
CREATE INDEX IF NOT EXISTS idx_parts_req_created ON parts_requests(created_at DESC);
"""

SEED_CATEGORIES = """
-- Seed default spare part categories (English names)
INSERT INTO part_categories (name, category_type, compatible_drone_models, unit_cost, description, is_active, created_at)
VALUES
  ('Propeller Set - DJI Matrice 300 (4 blades)',
   'Propeller', '["DJI Matrice 300"]', 750000,
   'OEM replacement propeller set for DJI Matrice 300 RTK.', TRUE, NOW()),

  ('Propeller Set - Aurelia X6 Pro (6 blades)',
   'Propeller', '["Aurelia X6 Pro"]', 650000,
   'OEM replacement propeller set for Aurelia X6 Pro hexacopter.', TRUE, NOW()),

  ('Propeller Set - DJI FlyCart 30 (4 blades)',
   'Propeller', '["DJI FlyCart 30"]', 1200000,
   'OEM replacement propeller set for DJI FlyCart 30 heavy-lift drone.', TRUE, NOW()),

  ('Motor - DJI Matrice 300 (single unit)',
   'Motor', '["DJI Matrice 300"]', 3500000,
   'Brushless replacement motor for DJI Matrice 300 RTK.', TRUE, NOW()),

  ('Motor - Aurelia X6 Pro (single unit)',
   'Motor', '["Aurelia X6 Pro"]', 2800000,
   'Brushless replacement motor for Aurelia X6 Pro.', TRUE, NOW()),

  ('ESC Board - DJI Matrice 300',
   'ESC', '["DJI Matrice 300"]', 5500000,
   'Electronic Speed Controller replacement board for DJI Matrice 300 RTK.', TRUE, NOW()),

  ('ESC Board - Aurelia X6 Pro',
   'ESC', '["Aurelia X6 Pro"]', 4200000,
   'Electronic Speed Controller replacement board for Aurelia X6 Pro.', TRUE, NOW()),

  ('Flight Controller Board - DJI Matrice 300',
   'FlightController', '["DJI Matrice 300"]', 6500000,
   'Main flight controller replacement for DJI Matrice 300 RTK. Includes IMU and compass.', TRUE, NOW()),

  ('Signal Cable ESC-Motor (1m)',
   'Cable', NULL, 85000,
   'Signal cable connecting ESC to motor. Universal fit for most drone models.', TRUE, NOW()),

  ('Power Cable Battery (1m)',
   'Cable', NULL, 120000,
   'Power cable connecting battery to ESC/FC power distribution board.', TRUE, NOW()),

  ('GPS/GNSS Sensor Module',
   'Sensor', '["DJI Matrice 300", "DJI FlyCart 30"]', 4200000,
   'Replacement GPS/GNSS module for DJI Matrice 300 and DJI FlyCart 30. Includes compass.', TRUE, NOW()),

  ('Arm Frame - DJI Matrice 300',
   'Frame', '["DJI Matrice 300"]', 2800000,
   'Replacement arm (propeller boom) for DJI Matrice 300 RTK. Replace after collision damage.', TRUE, NOW())

ON CONFLICT (name) DO NOTHING;
"""


def run():
    print("=" * 50)
    print("  Migrate: Inventory Schema")
    print("=" * 50)
    with engine.connect() as conn:
        with conn.begin():
            print("[1] Tạo enums & tables...")
            conn.execute(text(DDL))
            print("    OK")
            print("[2] Seed danh mục linh kiện mặc định...")
            conn.execute(text(SEED_CATEGORIES))
            print("    OK")

    print("\nMigration hoàn tất!")
    print("Tables: part_categories, hub_inventories, inventory_transactions, parts_requests")


if __name__ == "__main__":
    run()
