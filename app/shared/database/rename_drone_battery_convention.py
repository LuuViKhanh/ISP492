"""
Migration: đổi tên drone và serial_number pin theo quy tắc:
  - Drone name + serial_number  → "{model}-DRN001", "{model}-DRN002", ...
  - Battery serial_number       → "{battery_model}-BAT001", "{battery_model}-BAT002", ...

Lưu ý: Battery.id (PK) không thay đổi vì đang được FK tham chiếu.
Chỉ serial_number của battery được cập nhật.
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
            # ── 1. Rename Drones ─────────────────────────────────────────────
            print("[1] Đổi tên Drones theo quy tắc {model}-DRN{X:03d}...")

            # Lấy toàn bộ drone, nhóm theo model, đánh số thứ tự
            rows = conn.execute(text(
                "SELECT id, model FROM drones ORDER BY model, id"
            )).fetchall()

            # Đếm per-model
            model_counter: dict[str, int] = {}
            rename_map: list[tuple[str, str]] = []  # (drone_id, new_name)

            for drone_id, model in rows:
                model_key = (model or "Unknown").strip()
                model_counter[model_key] = model_counter.get(model_key, 0) + 1
                seq = model_counter[model_key]
                new_name = f"{model_key}-DRN{seq:03d}"
                rename_map.append((drone_id, new_name))

            # Trước khi UPDATE, xóa unique constraint tạm thời trên serial_number
            # bằng cách UPDATE lần lượt với giá trị temp trước
            for drone_id, new_name in rename_map:
                conn.execute(text(
                    "UPDATE drones SET name = :name, serial_number = :sn WHERE id = :id"
                ), {"name": new_name, "sn": f"_TMP_{drone_id}", "id": drone_id})

            # Sau đó gán giá trị thật
            for drone_id, new_name in rename_map:
                conn.execute(text(
                    "UPDATE drones SET serial_number = :sn WHERE id = :id"
                ), {"sn": new_name, "id": drone_id})

            print(f"    OK — đã đổi tên {len(rename_map)} drone")
            for drone_id, new_name in rename_map[:5]:
                print(f"      Drone {drone_id} → {new_name}")
            if len(rename_map) > 5:
                print(f"      ... và {len(rename_map) - 5} drone khác")

            # ── 2. Rename Battery serial_number ──────────────────────────────
            print("\n[2] Đổi serial_number Battery theo quy tắc {battery_model}-BAT{X:03d}...")

            # Bước 1: Set temp để tránh unique constraint conflict
            conn.execute(text(
                "UPDATE batteries SET serial_number = '_TMP_' || id"
            ))

            # Bước 2: Bulk UPDATE dùng ROW_NUMBER() per battery_model
            conn.execute(text("""
                WITH ranked AS (
                    SELECT
                        id,
                        COALESCE(battery_model, 'Unknown') AS bmodel,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(battery_model, 'Unknown')
                            ORDER BY id
                        ) AS seq
                    FROM batteries
                )
                UPDATE batteries b
                SET serial_number = r.bmodel || '-BAT' || LPAD(r.seq::text, 3, '0')
                FROM ranked r
                WHERE b.id = r.id
            """))

            count_bat = conn.execute(text("SELECT COUNT(*) FROM batteries")).scalar()
            print(f"    OK — đã đổi serial_number {count_bat} battery")

            # Hiển thị mẫu
            samples = conn.execute(text(
                "SELECT id, battery_model, serial_number FROM batteries ORDER BY battery_model, id LIMIT 5"
            )).fetchall()
            for bat_id, bmodel, sn in samples:
                print(f"      Battery {bat_id} ({bmodel}) → serial: {sn}")

    print("\n✅ Hoàn tất rename drone & battery.")


if __name__ == "__main__":
    run()
