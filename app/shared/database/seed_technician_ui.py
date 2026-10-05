import sys
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import asyncio
from sqlalchemy import text
from app.database.db import AsyncSessionLocal
from app.modules.missions.models import MissionStatus, HandlingStatus

async def seed_technician_ui():
    print("Bat dau nap du lieu Mock cho giao dien Technician (Hub 3)...")
    
    now = datetime.now()
    
    async with AsyncSessionLocal() as db:
        # Lấy Drone tại Hub 3 để gán cho các chuyến bay (Drone 1 và Drone 2)
        res_drones = await db.execute(text("SELECT id FROM drones WHERE current_hub_id = 3 LIMIT 2"))
        drone_ids = [row[0] for row in res_drones.fetchall()]
        
        # Nếu không có drone nào ở Hub 3, lấy bất kỳ
        if len(drone_ids) < 2:
            res_any = await db.execute(text("SELECT id FROM drones LIMIT 2"))
            drone_ids = [row[0] for row in res_any.fetchall()]
            
        drone_1, drone_2 = drone_ids[0], drone_ids[1] if len(drone_ids) > 1 else drone_ids[0]

        # ---------------------------------------------------------
        # 1. TRANSIT MISSIONS (Đang bay đến Hub 3)
        # ---------------------------------------------------------
        print("-> Nạp dữ liệu: Transit Missions (Đang bay đến Hub 3)")
        await db.execute(text("""
            INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
            VALUES ('ORD-TECH-TRANSIT-1', 'test@test.com', 'Hàng đang bay tới', 'Standard', 1.5, 4, 3, 'IN_TRANSIT', NOW())
            ON CONFLICT (id) DO UPDATE SET status = 'IN_TRANSIT'
        """))
        
        await db.execute(text("""
            INSERT INTO missions (
                customer_id, order_code, order_id, status, handling_status, origin_hub_id, destination_hub_id, 
                drone_id, departed_at, scheduled_time, created_at, updated_at, payload_weight, distance_m, delivery_fee
            )
            VALUES (
                'test@test.com', 'ORD-TECH-TRANSIT-1', 'ORD-TECH-TRANSIT-1', :status, :handling, 4, 3,
                :drone_id, :departed, NOW(), NOW(), NOW(), 2.0, 5000, 100000
            )
        """), {
            "status": "In Progress", # MissionStatus.IN_PROGRESS
            "handling": "Incoming", # HandlingStatus.INCOMING
            "drone_id": drone_1,
            "departed": now - timedelta(minutes=15)
        })

        # ---------------------------------------------------------
        # 2. OUTBOUND DELIVERIES (Chờ bay đi từ Hub 3)
        # ---------------------------------------------------------
        print("-> Nap du lieu: Outbound Deliveries (Tu Hub 3)")
        outbounds = [
            ("ORD-TECH-OUT-1", "Incoming"), # Khách chuẩn bị mang tới
            ("ORD-TECH-OUT-2", "At hub"),   # Đã nhận tại kho, chờ chuẩn bị pin
            ("ORD-TECH-OUT-3", "Ready")     # Sẵn sàng bay
        ]
        
        for idx, (order_code, handling_status) in enumerate(outbounds):
            await db.execute(text("""
                INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
                VALUES (:oid, 'test@test.com', :label, 'Standard', 2.0, 3, 5, 'PENDING', NOW())
                ON CONFLICT (id) DO UPDATE SET status = 'PENDING'
            """), {"oid": order_code, "label": f"Hàng chờ gửi {idx+1}"})
            
            await db.execute(text("""
                INSERT INTO missions (
                    customer_id, order_code, order_id, status, handling_status, origin_hub_id, destination_hub_id, 
                    drone_id, scheduled_time, created_at, updated_at, payload_weight, distance_m, delivery_fee
                )
                VALUES (
                    'test@test.com', :oid, :oid, 'Scheduled', :handling, 3, 5,
                    :drone_id, NOW(), NOW(), NOW(), 2.0, 5000, 100000
                )
            """), {
                "oid": order_code,
                "handling": handling_status,
                "drone_id": drone_2 if handling_status == "Ready" else None
            })

        # ---------------------------------------------------------
        # 3. DELIVERY CONFIRMATIONS (Đã tới Hub 3, chờ khách lấy)
        # ---------------------------------------------------------
        print("-> Nap du lieu: Delivery Confirmations (Cho khach toi lay o Hub 3)")
        await db.execute(text("""
            INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
            VALUES ('ORD-TECH-CONFIRM-1', 'test@test.com', 'Hàng chờ nhận', 'Standard', 3.0, 6, 3, 'AWAITING_PICKUP', NOW())
            ON CONFLICT (id) DO UPDATE SET status = 'AWAITING_PICKUP'
        """))
        
        await db.execute(text("""
            INSERT INTO missions (
                customer_id, order_code, order_id, status, handling_status, origin_hub_id, destination_hub_id, 
                drone_id, departed_at, arrived_at, arrival_confirmed_at, scheduled_time, created_at, updated_at, payload_weight, distance_m, delivery_fee
            )
            VALUES (
                'test@test.com', 'ORD-TECH-CONFIRM-1', 'ORD-TECH-CONFIRM-1', 'In Progress', 'At hub', 6, 3,
                :drone_id, :departed, :arrived, NULL, NOW(), NOW(), NOW(), 2.0, 5000, 100000
            )
        """), {
            "drone_id": drone_1,
            "departed": now - timedelta(minutes=45),
            "arrived": now - timedelta(minutes=5)
        })

        await db.commit()
        print("✅ Đã tạo thành công dữ liệu cho 3 màn hình Technician (Transit, Outbound, Confirmations).")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed_technician_ui())
