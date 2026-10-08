import sys
import os
import random
from datetime import datetime, timedelta

# Đảm bảo đường dẫn import
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import asyncio
from sqlalchemy import text
from app.database.db import AsyncSessionLocal

async def seed_all_technicians():
    print("Bat dau nap du lieu cho TAT CA cac Hub...")
    now = datetime.now()
    
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM telemetry_logs WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM mission_legs WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM payments WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM mission_reports WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM mission_hub_checkpoints WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM mission_battery_swap_history WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM incidents WHERE mission_id IN (SELECT id FROM missions WHERE order_code LIKE 'ORD-%')"))
        await db.execute(text("DELETE FROM maintenance_alerts WHERE mission_id IN (SELECT cast(id as text) FROM missions WHERE order_code LIKE 'ORD-%')"))
        
        await db.execute(text("DELETE FROM missions WHERE order_code LIKE 'ORD-%'"))
        await db.execute(text("DELETE FROM maintenance_schedules WHERE drone_id IN (SELECT id FROM drones)"))
        await db.execute(text("DELETE FROM work_orders WHERE drone_id IN (SELECT id FROM drones)"))
        
        res_hubs = await db.execute(text("SELECT id FROM hubs"))
        hub_ids = [row[0] for row in res_hubs.fetchall()]
        
        res_drones = await db.execute(text("SELECT id FROM drones"))
        drone_ids = [row[0] for row in res_drones.fetchall()]
        if not drone_ids:
            print("Khong co drone nao trong DB!")
            return
            
        res_bats = await db.execute(text("SELECT id FROM batteries"))
        bat_ids = [row[0] for row in res_bats.fetchall()]
        if not bat_ids:
            print("Khong co pin nao trong DB!")
            return
        b1 = bat_ids[0]

        for did in drone_ids:
            await db.execute(text("""
                INSERT INTO work_orders (drone_id, battery_id, technician_id, issue_description, status, completed_at, resolved_at)
                VALUES (:did, :bat_id, 'tech@example.com', 'Bao tri dinh ky', 'Completed', NOW() - INTERVAL '30 days', NOW() - INTERVAL '30 days')
            """), {"did": did, "bat_id": b1})
            
            await db.execute(text("""
                INSERT INTO maintenance_schedules (
                    drone_id, maintenance_type, interval_days, next_inspection_at, status, created_at
                )
                VALUES (:did, 'Periodic', 30, NOW() + INTERVAL '30 days', 'Scheduled', NOW())
            """), {"did": did})

        for hub_id in hub_ids:
            other_hubs = [h for h in hub_ids if h != hub_id]
            if not other_hubs:
                continue
                
            orig = random.choice(other_hubs)
            drone_1 = random.choice(drone_ids)
            drone_2 = random.choice(drone_ids)
            drone_3 = random.choice(drone_ids)

            code_transit = f"ORD-{hub_id}-TRANSIT"
            mission_transit = f"MIS-{hub_id}-TRANSIT"
            bat_1 = random.choice(bat_ids)
            await db.execute(text("""
                INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
                VALUES (:oid, 'test@test.com', 'Hang dang bay toi', 'Standard', 1.5, :orig, :dest, 'IN_TRANSIT', NOW())
                ON CONFLICT (id) DO UPDATE SET status = 'IN_TRANSIT'
            """), {"oid": code_transit, "orig": orig, "dest": hub_id})
            
            await db.execute(text("""
                INSERT INTO missions (
                    customer_id, order_code, mission_code, order_id, status, handling_status, origin_hub_id, destination_hub_id, 
                    drone_id, battery_id, departed_at, scheduled_time, created_at, updated_at, payload_weight, distance_m, delivery_fee
                ) VALUES (
                    'test@test.com', :oid, :mcode, :oid, 'In Progress', 'Incoming', :orig, :dest,
                    :drone_id, :bat_id, :departed, NOW(), NOW(), NOW(), 2.0, 5000, 100000
                )
            """), {"oid": code_transit, "mcode": mission_transit, "orig": orig, "dest": hub_id, "drone_id": drone_1, "bat_id": bat_1, "departed": now - timedelta(minutes=15)})

            outbounds = [
                (f"ORD-{hub_id}-OUT-1", f"MIS-{hub_id}-OUT-1", "Incoming"), 
                (f"ORD-{hub_id}-OUT-2", f"MIS-{hub_id}-OUT-2", "At hub"),   
                (f"ORD-{hub_id}-OUT-3", f"MIS-{hub_id}-OUT-3", "Ready")     
            ]
            for idx, (order_code, mcode, handling_status) in enumerate(outbounds):
                dest = random.choice(other_hubs)
                bat_2 = random.choice(bat_ids) if handling_status == "Ready" else None
                await db.execute(text("""
                    INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
                    VALUES (:oid, 'test@test.com', :label, 'Standard', 2.0, :orig, :dest, 'PENDING', NOW())
                    ON CONFLICT (id) DO UPDATE SET status = 'PENDING'
                """), {"oid": order_code, "label": f"Hang cho gui {idx+1}", "orig": hub_id, "dest": dest})
                
                await db.execute(text("""
                    INSERT INTO missions (
                        customer_id, order_code, mission_code, order_id, status, handling_status, origin_hub_id, destination_hub_id, 
                        drone_id, battery_id, scheduled_time, created_at, updated_at, payload_weight, distance_m, delivery_fee
                    ) VALUES (
                        'test@test.com', :oid, :mcode, :oid, 'Scheduled', :handling, :orig, :dest,
                        :drone_id, :bat_id, NOW(), NOW(), NOW(), 2.0, 5000, 100000
                    )
                """), {"oid": order_code, "mcode": mcode, "handling": handling_status, "orig": hub_id, "dest": dest, "drone_id": drone_2 if handling_status == "Ready" else None, "bat_id": bat_2})

            code_confirm = f"ORD-{hub_id}-CONFIRM"
            mcode_confirm = f"MIS-{hub_id}-CONFIRM"
            orig2 = random.choice(other_hubs)
            bat_3 = random.choice(bat_ids)
            await db.execute(text("""
                INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
                VALUES (:oid, 'test@test.com', 'Hang cho nhan', 'Standard', 3.0, :orig, :dest, 'AWAITING_PICKUP', NOW())
                ON CONFLICT (id) DO UPDATE SET status = 'AWAITING_PICKUP'
            """), {"oid": code_confirm, "orig": orig2, "dest": hub_id})
            
            await db.execute(text("""
                INSERT INTO missions (
                    customer_id, order_code, mission_code, order_id, status, handling_status, origin_hub_id, destination_hub_id, 
                    drone_id, battery_id, departed_at, arrived_at, arrival_confirmed_at, scheduled_time, created_at, updated_at, payload_weight, distance_m, delivery_fee
                ) VALUES (
                    'test@test.com', :oid, :mcode, :oid, 'In Progress', 'At hub', :orig, :dest,
                    :drone_id, :bat_id, :departed, :arrived, NULL, NOW(), NOW(), NOW(), 2.0, 5000, 100000
                )
            """), {"oid": code_confirm, "mcode": mcode_confirm, "orig": orig2, "dest": hub_id, "drone_id": drone_3, "bat_id": bat_3, "departed": now - timedelta(minutes=45), "arrived": now - timedelta(minutes=5)})

        await db.commit()
        print(f"✅ Xong! Da tao mock data bao tri va giao hang cho tat ca {len(hub_ids)} Hubs thanh cong.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed_all_technicians())
