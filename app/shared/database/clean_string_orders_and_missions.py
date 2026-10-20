"""
clean_string_orders_and_missions.py
───────────────────────────────────
Script xoa sach cac don hang va nhiem vu rac chua 'string'
ma khong anh huong den Hubs, Drones, Batteries, Users, hay cac chuc nang khac.
"""

import os
import sys
import asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def clean_database():
    print("=" * 60)
    print("BAT DAU DON DEP DON HANG & NHIEM VU RAC (CHUA 'string')")
    print("=" * 60)

    engine = create_async_engine(DATABASE_URL, connect_args={"statement_cache_size": 0})
    async with engine.begin() as conn:
        # 1. Tim cac order_id chua 'string'
        rows = (await conn.execute(text("""
            SELECT id FROM orders 
            WHERE package_label LIKE '%string%' 
               OR package_type LIKE '%string%'
               OR package_label IS NULL
        """))).fetchall()
        target_order_ids = [r[0] for r in rows]
        print(f"-> Phat hien {len(target_order_ids)} don hang rac can xoa.")

        if not target_order_ids:
            print("Khong tim thay don hang rac nao can xoa.")
            return

        # 2. Tim tat ca mission_id lien ket voi cac order_id nay
        mission_rows = (await conn.execute(text("""
            SELECT id FROM missions WHERE order_id = ANY(:order_ids)
        """), {"order_ids": target_order_ids})).fetchall()
        target_mission_ids = [r[0] for r in mission_rows]
        print(f"-> Phat hien {len(target_mission_ids)} nhiem vu (missions) lien ket can xoa.")

        # 3. Xoa cac bang con phu thuoc mission_id truoc (tranh loi Foreign Key)
        if target_mission_ids:
            # telemetry_logs
            del_tele = (await conn.execute(text("""
                DELETE FROM telemetry_logs WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_tele} telemetry_logs.")

            # mission_legs
            del_legs = (await conn.execute(text("""
                DELETE FROM mission_legs WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_legs} mission_legs.")

            # mission_hub_checkpoints
            del_chk = (await conn.execute(text("""
                DELETE FROM mission_hub_checkpoints WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_chk} mission_hub_checkpoints.")

            # ai_predictions
            del_pred = (await conn.execute(text("""
                DELETE FROM ai_predictions WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_pred} ai_predictions.")

            # ai_recommendations
            del_rec = (await conn.execute(text("""
                DELETE FROM ai_recommendations WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_rec} ai_recommendations.")

            # incidents
            del_inc = (await conn.execute(text("""
                DELETE FROM incidents WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_inc} incidents.")

            # mission_battery_swap_history
            del_swap = (await conn.execute(text("""
                DELETE FROM mission_battery_swap_history WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_swap} mission_battery_swap_history.")

            # mission_reports
            del_rep = (await conn.execute(text("""
                DELETE FROM mission_reports WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_rep} mission_reports.")

            # payments
            del_pay = (await conn.execute(text("""
                DELETE FROM payments WHERE mission_id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_pay} payments.")

            # missions
            del_mis = (await conn.execute(text("""
                DELETE FROM missions WHERE id = ANY(:m_ids)
            """), {"m_ids": target_mission_ids})).rowcount
            print(f"   + Da xoa {del_mis} missions.")

        # 4. Xoa orders
        del_ord = (await conn.execute(text("""
            DELETE FROM orders WHERE id = ANY(:order_ids)
        """), {"order_ids": target_order_ids})).rowcount
        print(f"   + Da xoa {del_ord} orders.")

        print("\n-> DA XOA THANH CONG TOAN BO DU LIEU RAC! Khong con don hang 'string' nao.")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(clean_database())
