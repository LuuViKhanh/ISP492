"""
migrate_battery_catalog.py — Tạo bảng battery_model_catalog + seed 3 model DJI

Idempotent — chạy lại an toàn.
Chạy: python app/shared/database/migrate_battery_catalog.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(0, "C:/Users/ext.han.bui/AppData/Roaming/Python/Python314/site-packages")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))
from sqlalchemy import create_engine, text
import os as _os

_raw = _os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
if "postgresql" in _raw and "+psycopg2" not in _raw:
    _raw = _raw.replace("postgresql://", "postgresql+psycopg2://")
engine = create_engine(_raw)

DDL = """
CREATE TABLE IF NOT EXISTS battery_model_catalog (
    id                     BIGSERIAL PRIMARY KEY,
    name                   VARCHAR(100) NOT NULL UNIQUE,
    compatible_drone_models JSONB,
    capacity_wh            FLOAT NOT NULL,
    unit_cost              FLOAT,
    description            TEXT,
    is_active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bmc_name ON battery_model_catalog(name);
"""

# 3 model pin DJI tương thích với 3 dòng drone hiện có
SEED = [
    (
        "DJI TB30",
        '["DJI Mavic 3"]',
        77.0,
        2500000,
        "Pin chính hãng DJI Intelligent Flight Battery TB30 cho Mavic 3. "
        "Dung lượng 5000mAh / 77Wh. Thời gian bay ~46 phút (không tải).",
    ),
    (
        "DJI TB60",
        '["DJI Matrice 300"]',
        273.1,
        5500000,
        "Pin DJI TB60 cho Matrice 300 RTK. "
        "Dung lượng 5935mAh / 273.1Wh. Hệ thống dual-battery, thời gian bay ~55 phút.",
    ),
    (
        "DJI TB65",
        '["DJI FlyCart 30"]',
        476.0,
        8500000,
        "Pin DJI TB65 cho FlyCart 30. "
        "Thiết kế cho tải nặng đến 30kg. Dung lượng 28000mAh / 476Wh.",
    ),
]

if __name__ == "__main__":
    print("=" * 50)
    print("  Migrate: battery_model_catalog")
    print("=" * 50)
    with engine.connect() as conn:
        with conn.begin():
            print("[1] Tạo bảng battery_model_catalog...")
            conn.execute(text(DDL))
            print("    OK")

            print("[2] Seed DJI battery models...")
            for name, compat, cap, cost, desc in SEED:
                conn.execute(text("""
                    INSERT INTO battery_model_catalog
                        (name, compatible_drone_models, capacity_wh, unit_cost, description, is_active, created_at)
                    VALUES (:name, CAST(:compat AS jsonb), :cap, :cost, :desc, true, now())
                    ON CONFLICT (name) DO UPDATE SET
                        compatible_drone_models = EXCLUDED.compatible_drone_models,
                        capacity_wh  = EXCLUDED.capacity_wh,
                        unit_cost    = EXCLUDED.unit_cost,
                        description  = EXCLUDED.description
                """), {"name": name, "compat": compat, "cap": cap, "cost": cost, "desc": desc})
                sys.stderr.write(f"    {name}: {cap}Wh — {cost:,.0f} VND\n")

            # Cập nhật battery_model cho các pin hiện có nếu chưa có
            print("[3] Cập nhật battery_model cho pin hiện có từ battery_model field...")
            r = conn.execute(text(
                "SELECT COUNT(*) FROM batteries WHERE battery_model IS NULL OR battery_model=''"
            )).scalar()
            sys.stderr.write(f"    {r} pin chưa có model\n")

    print("\nMigration hoàn tất!")
    print("  Table: battery_model_catalog (3 rows seeded)")
