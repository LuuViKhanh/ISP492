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
-- Seed danh mục linh kiện mặc định
INSERT INTO part_categories (name, category_type, compatible_drone_models, unit_cost, description, is_active, created_at)
VALUES
  ('Cánh quạt DJI Mavic 3 (1 bộ 4 cánh)',
   'Propeller', '["DJI Mavic 3"]', 350000,
   'Bộ 4 cánh thay thế chính hãng cho DJI Mavic 3', TRUE, NOW()),

  ('Cánh quạt DJI Matrice 300 (1 bộ 4 cánh)',
   'Propeller', '["DJI Matrice 300"]', 750000,
   'Bộ 4 cánh thay thế cho DJI Matrice 300 RTK', TRUE, NOW()),

  ('Cánh quạt DJI FlyCart 30 (1 bộ 4 cánh)',
   'Propeller', '["DJI FlyCart 30"]', 1200000,
   'Bộ 4 cánh thay thế cho DJI FlyCart 30', TRUE, NOW()),

  ('Động cơ DJI Mavic 3 (1 motor)',
   'Motor', '["DJI Mavic 3"]', 1800000,
   'Động cơ brushless thay thế cho Mavic 3', TRUE, NOW()),

  ('Động cơ DJI Matrice 300 (1 motor)',
   'Motor', '["DJI Matrice 300"]', 3500000,
   'Động cơ thay thế cho Matrice 300 RTK', TRUE, NOW()),

  ('Bo mạch ESC DJI Mavic 3',
   'ESC', '["DJI Mavic 3"]', 2500000,
   'Electronic Speed Controller dự phòng cho Mavic 3', TRUE, NOW()),

  ('Bo mạch ESC DJI Matrice 300',
   'ESC', '["DJI Matrice 300"]', 5500000,
   'ESC dự phòng cho Matrice 300', TRUE, NOW()),

  ('Bo mạch điều khiển bay (Flight Controller) DJI Mavic 3',
   'FlightController', '["DJI Mavic 3"]', 6500000,
   'Flight controller mainboard dự phòng', TRUE, NOW()),

  ('Cáp tín hiệu ESC-Motor (1m)',
   'Cable', NULL, 85000,
   'Cáp tín hiệu ESC sang động cơ, dùng chung các model', TRUE, NOW()),

  ('Cáp nguồn pin (1m)',
   'Cable', NULL, 120000,
   'Cáp nguồn nối battery tới ESC/FC', TRUE, NOW()),

  ('Cảm biến GPS dự phòng',
   'Sensor', '["DJI Matrice 300", "DJI FlyCart 30"]', 4200000,
   'Module GPS/GNSS dự phòng cho drone lớn', TRUE, NOW()),

  ('Khung cánh (Arm) DJI Matrice 300',
   'Frame', '["DJI Matrice 300"]', 2800000,
   'Arm thay thế khi bị cong/gãy do va chạm', TRUE, NOW())

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
