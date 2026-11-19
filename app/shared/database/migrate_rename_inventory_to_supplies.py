"""
migrate_rename_inventory_to_supplies.py

Đổi tên bảng:
  hub_inventories       → hub_supplies
  inventory_transactions → supply_transactions

Thêm enum mới:
  supply_txn_reason_enum (clone từ txn_reason_enum)

Idempotent — chạy lại an toàn.
Chạy: python app/shared/database/migrate_rename_inventory_to_supplies.py
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

if __name__ == "__main__":
    print("=" * 55)
    print("  Migrate: rename inventory tables → supplies")
    print("=" * 55)
    with engine.connect() as conn:
        with conn.begin():

            # ── 1. hub_inventories → hub_supplies ───────────────────
            exists_old = conn.execute(text(
                "SELECT to_regclass('public.hub_inventories')"
            )).scalar()
            exists_new = conn.execute(text(
                "SELECT to_regclass('public.hub_supplies')"
            )).scalar()

            if exists_old and not exists_new:
                print("[1] Đổi tên hub_inventories → hub_supplies ...")
                conn.execute(text("ALTER TABLE hub_inventories RENAME TO hub_supplies"))
                conn.execute(text(
                    "ALTER INDEX IF EXISTS uq_hub_part RENAME TO uq_hub_supply_part"
                ))
                print("    OK")
            elif exists_new:
                print("[1] hub_supplies đã tồn tại — bỏ qua")
            else:
                print("[1] hub_inventories không tồn tại — bỏ qua")

            # ── 2. inventory_transactions → supply_transactions ──────
            exists_old2 = conn.execute(text(
                "SELECT to_regclass('public.inventory_transactions')"
            )).scalar()
            exists_new2 = conn.execute(text(
                "SELECT to_regclass('public.supply_transactions')"
            )).scalar()

            if exists_old2 and not exists_new2:
                print("[2] Đổi tên inventory_transactions → supply_transactions ...")
                conn.execute(text("ALTER TABLE inventory_transactions RENAME TO supply_transactions"))
                print("    OK")
            elif exists_new2:
                print("[2] supply_transactions đã tồn tại — bỏ qua")
            else:
                print("[2] inventory_transactions không tồn tại — bỏ qua")

            # ── 3. Tạo enum supply_txn_reason_enum ──────────────────
            print("[3] Tạo enum supply_txn_reason_enum ...")
            conn.execute(text("""
                DO $$ BEGIN
                    CREATE TYPE supply_txn_reason_enum AS ENUM (
                        'Receive', 'RepairUse', 'TransferIn', 'TransferOut', 'Adjust', 'Discard'
                    );
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
            """))
            # Cập nhật cột reason trong supply_transactions dùng enum mới
            conn.execute(text("""
                ALTER TABLE supply_transactions
                    ALTER COLUMN reason TYPE supply_txn_reason_enum
                    USING reason::text::supply_txn_reason_enum;
            """))
            print("    OK")

            # ── 4. Verify ────────────────────────────────────────────
            print("\n[Verify]")
            for tbl in ["hub_supplies", "supply_transactions", "part_categories", "parts_requests"]:
                exists = conn.execute(text(f"SELECT to_regclass('public.{tbl}')")).scalar()
                print(f"  {tbl}: {'OK' if exists else 'MISSING'}")

    print("\nMigration hoàn tất!")
