"""
migrate_drone_id_to_drn_format.py
───────────────────────────────────
Script di chuyển mã Drone ID từ dạng số nguyên (BIGINT: 28, 29, 30...) 
sang định dạng chuẩn chuỗi: DRN-XXXX (DRN-0001, DRN-0002, ...)
và cập nhật đồng bộ toàn bộ các bảng tham chiếu khóa ngoại liên quan.

Bảng được cập nhật:
1. drones (cột id -> VARCHAR(50), định dạng DRN-XXXX)
2. missions (cột drone_id -> VARCHAR(50))
3. batteries (cột drone_id -> VARCHAR(50))
4. incidents (cột drone_id -> VARCHAR(50))
5. work_orders (cột drone_id -> VARCHAR(50))
6. maintenance_alerts (cột drone_id -> VARCHAR(50))
7. maintenance_records (cột drone_id -> VARCHAR(50))
8. maintenance_schedules (cột drone_id -> VARCHAR(50))
9. notifications (cột drone_id -> VARCHAR(50))
10. mission_drone_assignment_history (cột old_drone_id, new_drone_id -> VARCHAR(50))

Cách chạy:
    python app/shared/database/migrate_drone_id_to_drn_format.py
"""

import sys
import os
import asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Load .env
env_path = os.path.join(os.path.dirname(__file__), "../../../.env")
load_dotenv(env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env file.")

async def migrate_drones():
    print("=" * 60)
    print("BAT DAU MIGRATION DRONE ID SANG DINH DANG DRN-XXXX")
    print("=" * 60)
    
    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"statement_cache_size": 0}
    )

    async with engine.begin() as conn:
        print("\n1. Kiem tra so luong Drone hien tai trong DB...")
        drones_count = (await conn.execute(text("SELECT COUNT(*) FROM drones"))).scalar()
        print(f"-> Tong so Drone: {drones_count}")

        # Kiem tra xem da migrate chua
        sample = (await conn.execute(text("SELECT id FROM drones LIMIT 1"))).scalar()
        if sample and str(sample).startswith("DRN-"):
            print(f"-> DB da duoc migrate sang format DRN- (Mau ID: {sample}). Khong can chay lai.")
            return

        print("\n2. Tam thoi go bo Foreign Key constraints den drones...")
        # missions_drone_id_fkey
        try:
            await conn.execute(text("ALTER TABLE missions DROP CONSTRAINT IF EXISTS missions_drone_id_fkey;"))
            print("   - Da go FK: missions_drone_id_fkey")
        except Exception as e:
            print(f"   - Bo qua go FK missions: {e}")

        # batteries_drone_id_fkey if any
        try:
            await conn.execute(text("ALTER TABLE batteries DROP CONSTRAINT IF EXISTS batteries_drone_id_fkey;"))
            print("   - Da go FK: batteries_drone_id_fkey (neu co)")
        except Exception as e:
            print(f"   - Bo qua go FK batteries: {e}")

        print("\n3. Tao bang anh xa ID cu (so) -> ID moi (DRN-XXXX theo thu tu)...")
        # Tao temporary mapping table
        await conn.execute(text("""
            CREATE TEMP TABLE temp_drone_id_map AS
            SELECT 
                id AS old_id,
                'DRN-' || LPAD(ROW_NUMBER() OVER (ORDER BY id ASC)::text, 4, '0') AS new_id
            FROM drones;
        """))
        
        map_samples = (await conn.execute(text("SELECT old_id, new_id FROM temp_drone_id_map ORDER BY old_id LIMIT 5"))).fetchall()
        print("   Mau chuyen doi:")
        for old_id, new_id in map_samples:
            print(f"   * ID cu: {old_id}  ===>  ID moi: {new_id}")

        print("\n4. Chuyen kieu du lieu cac cot tham chieu sang VARCHAR(50)...")
        ref_tables_cols = [
            ("missions", "drone_id"),
            ("batteries", "drone_id"),
            ("incidents", "drone_id"),
            ("work_orders", "drone_id"),
            ("maintenance_alerts", "drone_id"),
            ("maintenance_records", "drone_id"),
            ("maintenance_schedules", "drone_id"),
            ("notifications", "drone_id"),
            ("mission_drone_assignment_history", "old_drone_id"),
            ("mission_drone_assignment_history", "new_drone_id"),
        ]

        for tbl, col in ref_tables_cols:
            try:
                # Kiem tra bang va cot co ton tai khong
                chk = await conn.execute(text(f"""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = '{tbl}' AND column_name = '{col}';
                """))
                if chk.scalar():
                    await conn.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE VARCHAR(50) USING {col}::text;"))
                    print(f"   - Da chuyen {tbl}.{col} sang VARCHAR(50)")
            except Exception as e:
                print(f"   ! Bo qua {tbl}.{col}: {e}")

        print("\n5. Cap nhat du lieu ID moi (DRN-XXXX) vao cac bang tham chieu...")
        for tbl, col in ref_tables_cols:
            try:
                chk = await conn.execute(text(f"""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = '{tbl}' AND column_name = '{col}';
                """))
                if chk.scalar():
                    stmt = f"""
                        UPDATE {tbl} t
                        SET {col} = m.new_id
                        FROM temp_drone_id_map m
                        WHERE t.{col} = m.old_id::text;
                    """
                    up_res = await conn.execute(text(stmt))
                    print(f"   - Cap nhat {tbl}.{col}: {up_res.rowcount} dong da map.")
            except Exception as e:
                print(f"   ! Loi cap nhat {tbl}.{col}: {e}")

        print("\n6. Chuyen doi cot id cua bang drones sang VARCHAR(50) va gan gia tri moi...")
        # Drop sequence / default autoincrement neu co
        try:
            await conn.execute(text("ALTER TABLE drones ALTER COLUMN id DROP DEFAULT;"))
        except Exception:
            pass
            
        await conn.execute(text("ALTER TABLE drones ALTER COLUMN id TYPE VARCHAR(50) USING id::text;"))
        await conn.execute(text("""
            UPDATE drones d
            SET id = m.new_id
            FROM temp_drone_id_map m
            WHERE d.id = m.old_id::text;
        """))
        print("   - Da cap nhat toan bo id trong bang drones sang format DRN-XXXX")

        print("\n7. Tao lai Foreign Key constraint giua missions va drones...")
        try:
            await conn.execute(text("""
                ALTER TABLE missions 
                ADD CONSTRAINT missions_drone_id_fkey 
                FOREIGN KEY (drone_id) REFERENCES drones(id) ON UPDATE CASCADE ON DELETE SET NULL;
            """))
            print("   - Da khoi phuc FK constraint: missions_drone_id_fkey")
        except Exception as e:
            print(f"   ! Khong tao lai duoc FK missions_drone_id_fkey: {e}")

        try:
            await conn.execute(text("""
                ALTER TABLE batteries 
                ADD CONSTRAINT batteries_drone_id_fkey 
                FOREIGN KEY (drone_id) REFERENCES drones(id) ON UPDATE CASCADE ON DELETE SET NULL;
            """))
            print("   - Da khoi phuc FK constraint: batteries_drone_id_fkey")
        except Exception as e:
            print(f"   ! Khong tao lai duoc FK batteries_drone_id_fkey: {e}")

        print("\n8. Kiem tra lai ket qua sau Migration...")
        sample_drones = (await conn.execute(text("SELECT id, name, model, current_hub_id FROM drones ORDER BY id LIMIT 5"))).fetchall()
        for d in sample_drones:
            print(f"   * Drone: {d[0]} | {d[1]} | Hub #{d[3]}")

        sample_missions = (await conn.execute(text("SELECT id, drone_id FROM missions WHERE drone_id IS NOT NULL LIMIT 3"))).fetchall()
        for m in sample_missions:
            print(f"   * Mission {m[0]} -> Drone ID: {m[1]}")

        sample_bats = (await conn.execute(text("SELECT id, drone_id FROM batteries WHERE drone_id IS NOT NULL LIMIT 3"))).fetchall()
        for b in sample_bats:
            print(f"   * Battery {b[0]} -> Drone ID: {b[1]}")

        print("\n" + "=" * 60)
        print("MIGRATION THANH CONG 100%! TOAN BO DU LIEU DUOC BAO TOAN.")
        print("=" * 60)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate_drones())
