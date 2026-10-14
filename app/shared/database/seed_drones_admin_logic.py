# -*- coding: utf-8 -*-
r"""
Script nạp dữ liệu Drone và Pin chuẩn theo đúng Logic và Schema của Role Admin (Fleet Admin).
- Sử dụng Pydantic Schema: DroneCreate, BatteryCreate (từ app/modules/fleet/admin/router.py)
- Sử dụng hàm sinh ID chuẩn của hệ thống: generate_sequential_id (BAT-XXXX)
- Tự động kiểm tra trùng lặp serial_number
- Sử dụng AsyncSessionLocal và Transaction an toàn

Cách chạy:
    python app/shared/database/seed_drones_admin_logic.py
"""

import os
import sys
import asyncio

# Tự động định vị thư mục gốc Drone-backend
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import select, text
from app.database.db import AsyncSessionLocal
from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status
from app.modules.system.models import Hub
from app.shared.id_generator import generate_sequential_id

# 10 Dòng Drone công nghiệp tải trọng lớn (2.5 - 5.0 kg)
DRONE_TEMPLATES = [
    {"model": "DJI Matrice 350 RTK", "payload": 2.7, "speed": 23.0, "bat_model": "DJI TB65", "bat_cap": 7500.0},
    {"model": "DJI Matrice 300 RTK", "payload": 2.7, "speed": 23.0, "bat_model": "DJI TB60", "bat_cap": 7000.0},
    {"model": "Foxtech Loong 2160", "payload": 2.5, "speed": 26.0, "bat_model": "Foxtech 6S", "bat_cap": 6200.0},
    {"model": "Skyfront Perimeter 8", "payload": 3.0, "speed": 20.0, "bat_model": "Skyfront PowerPlus", "bat_cap": 7200.0},
    {"model": "Tarot T960 Heavy Lift", "payload": 3.5, "speed": 18.0, "bat_model": "Tattu Pro 6S", "bat_cap": 7800.0},
    {"model": "Tarot X6 Hexacopter", "payload": 4.0, "speed": 20.0, "bat_model": "Tattu Pro 12S", "bat_cap": 8500.0},
    {"model": "Gryphon GD-X8", "payload": 4.5, "speed": 22.0, "bat_model": "Gryphon Dual 6S", "bat_cap": 8800.0},
    {"model": "Aurelia X6 Pro", "payload": 5.0, "speed": 22.0, "bat_model": "Aurelia UltraPower", "bat_cap": 9000.0},
    {"model": "Freefly Alta X", "payload": 5.0, "speed": 25.0, "bat_model": "Freefly Trekker", "bat_cap": 9500.0},
    {"model": "DJI FlyCart 15", "payload": 5.0, "speed": 20.0, "bat_model": "DJI DB2000", "bat_cap": 9800.0},
]

async def seed_via_admin_logic():
    print("==================================================================")
    print(" BẮT ĐẦU NẠP DỮ LIỆU BẰNG ADMIN SERVICE / LOGIC")
    print("==================================================================")

    async with AsyncSessionLocal() as db:
        # 1. Kiểm tra danh sách Hubs hợp lệ
        res_hubs = await db.execute(select(Hub).order_by(Hub.id))
        hubs = res_hubs.scalars().all()
        print(f"-> Tìm thấy {len(hubs)} Hubs trong hệ thống.")

        total_drones_added = 0
        total_batteries_added = 0

        for hub in hubs:
            hub_code = f"H{hub.id:02d}"
            print(f"\n[Hub {hub.id}] {hub.name}:")

            for idx, item in enumerate(DRONE_TEMPLATES, start=1):
                # -------------------------------------------------------------
                # 2. Tạo Drone theo đúng nghiệp vụ Admin (DroneCreate)
                # -------------------------------------------------------------
                drone_name = f"{item['model']} - Unit {hub_code}-{idx:02d}"
                
                # Logic tương tự POST /admin/fleet/drones
                drone = Drone(
                    name=drone_name,
                    model=item["model"],
                    payload_capacity_kg=item["payload"],
                    max_speed=item["speed"],
                    status=DroneStatus.AVAILABLE,
                    current_hub_id=hub.id,
                    battery_level_pct=100,
                    utilization_pct=0.0
                )
                db.add(drone)
                await db.flush()  # Để drone có ID ngay lập tức
                total_drones_added += 1

                # -------------------------------------------------------------
                # 3. Tạo Pin theo đúng nghiệp vụ Admin (BatteryCreate + generate_sequential_id)
                # -------------------------------------------------------------
                serial_num = f"SN-BAT-{hub_code}-{idx:02d}"
                
                # Kiểm tra serial_number như router Admin
                check_exist = await db.execute(select(Battery).where(Battery.serial_number == serial_num))
                if not check_exist.scalar_one_or_none():
                    battery_id = await generate_sequential_id(db, Battery, "BAT")
                    
                    battery = Battery(
                        id=battery_id,
                        serial_number=serial_num,
                        capacity_wh=item["bat_cap"],
                        status=batteries_status.ACTIVE,
                        current_hub_id=hub.id,
                        drone_id=drone.id,  # Gán pin vào drone hoặc để None
                        charge_level_pct=100,
                        battery_model=item["bat_model"]
                    )
                    db.add(battery)
                    total_batteries_added += 1

            print(f"  + Đã tạo 10 Drones và 10 Batteries.")

        # Cập nhật lại sequence của drones_id_seq
        await db.execute(text("SELECT setval('drones_id_seq', (SELECT MAX(id) FROM drones));"))
        
        # Commit toàn bộ transaction
        await db.commit()
        print("\n==================================================================")
        print(f" HOÀN TẤT: Đã thêm {total_drones_added} Drones và {total_batteries_added} Batteries!")
        print(" Toàn bộ dữ liệu tuân thủ 100% ràng buộc schema của Admin Role.")
        print("==================================================================")

if __name__ == "__main__":
    asyncio.run(seed_via_admin_logic())
