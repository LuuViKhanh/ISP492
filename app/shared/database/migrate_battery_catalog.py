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
        "DJI-BAT-77W",
        '["DJI Mavic 3"]',
        77.0,
        2500000,
        "DJI Intelligent Flight Battery (TB30) for Mavic 3. ~46 min flight time (no payload).",
    ),
    (
        "DJI-BAT-273W",
        '["DJI Matrice 300"]',
        273.1,
        5500000,
        "DJI TB60 battery for Matrice 300 RTK. Dual-battery system, ~55 min flight.",
    ),
    (
        "DJI-BAT-476W",
        '["DJI FlyCart 30"]',
        476.0,
        8500000,
        "DJI TB65 battery for FlyCart 30 heavy-lift. Up to 30kg payload.",
    ),
    (
        "DJI-BAT-130W",
        '["DJI Matrice 300"]',
        130.0,
        5800000,
        "DJI TB48D battery. Compatible with Matrice series.",
    ),
    (
        "AUR-BAT-444W",
        '["Aurelia X6 Pro"]',
        444.0,
        14500000,
        "Aurelia UltraPower battery for X6 Pro hexacopter.",
    ),
    (
        "DJI-BAT-980W",
        '["DJI Matrice 350 RTK"]',
        980.0,
        36000000,
        "DJI DB2000 intelligent flight battery for Matrice 350 RTK.",
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
