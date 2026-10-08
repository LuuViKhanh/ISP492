import asyncio
import os
import sys
import uuid
from datetime import datetime

# Thêm thư mục gốc vào path để import
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from app.database.db import async_engine
from sqlalchemy import text

async def seed():
    async with async_engine.begin() as conn:
        print("Xóa sạch dữ liệu mock cũ...")
        # Xóa dữ liệu cũ
        await conn.execute(text("TRUNCATE TABLE users, roles, drones, batteries, orders, missions, hubs CASCADE;"))
        
        print("Tạo dữ liệu Hubs...")
        hubs = [
            (1, "HUB-Q1", "Hub Quận 1 - Bến Thành", "1 Lê Lợi, Bến Thành, Q1", 10.7725, 106.7001),
            (2, "HUB-Q3", "Hub Quận 3 - Võ Văn Tần", "100 Võ Văn Tần, Q3", 10.7766, 106.6905),
            (3, "HUB-Q7", "Hub Quận 7 - Phú Mỹ Hưng", "10 Nguyễn Văn Linh, Q7", 10.7325, 106.7088),
            (4, "HUB-TD", "Hub Thủ Đức - Võ Văn Ngân", "1 Võ Văn Ngân, Thủ Đức", 10.8494, 106.7731),
            (5, "HUB-GV", "Hub Gò Vấp - Nguyễn Oanh", "1 Nguyễn Oanh, Gò Vấp", 10.8359, 106.6806),
            (6, "HUB-BTH", "Hub Bình Thạnh - Ung Văn Khiêm", "1 Ung Văn Khiêm, Bình Thạnh", 10.8142, 106.7115),
            (7, "HUB-TB", "Hub Tân Bình", "1 Tân Bình", 10.8015, 106.6521),
            (8, "HUB-BT", "Hub Bình Tân", "1 Bình Tân", 10.7455, 106.6134),
            (9, "HUB-NB", "Hub Nhà Bè", "1 Nhà Bè", 10.6820, 106.7354),
            (10, "HUB-TP", "Hub Tân Phú", "1 Tân Phú", 10.7900, 106.6280)
        ]
        for hid, code, name, addr, lat, lng in hubs:
            await conn.execute(text("""
                INSERT INTO hubs (id, code, name, address, latitude, longitude, status, created_at, updated_at)
                VALUES (:id, :code, :name, :addr, :lat, :lng, 'Active', NOW(), NOW())
            """), {"id": hid, "code": code, "name": name, "addr": addr, "lat": lat, "lng": lng})

        print("Tạo Roles...")
        roles = [
            ("OPERATOR", "Operator"),
            ("ADMIN", "System Administrator"),
            ("TECHNICIAN", "Technician"),
            ("CUSTOMER", "Customer")
        ]
        for rid, name in roles:
            await conn.execute(text("INSERT INTO roles (id, role_name, description) VALUES (:id, :name, :name)"), {"id": rid, "name": name})

        print("Tạo Users theo yêu cầu...")
        from app.modules.auth.service import hash_password
        pw_hash = hash_password("Password123!")
        
        users = [
            # id, role_id, full_name, email, hub_id
            (str(uuid.uuid4()), "OPERATOR", "Operator One", "operator@droneoptai.com", None),
            (str(uuid.uuid4()), "ADMIN", "System Administrator", "admin@drone.com", None),
            (str(uuid.uuid4()), "TECHNICIAN", "tech", "tech@example.com", 1), # Bến Thành
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Quận 3 - Võ Văn Tần", "tech_hubq3@example.com", 2),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Quận 7 - Phú Mỹ Hưng", "tech_hubq7@example.com", 3),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Thủ Đức - Võ Văn Ngân", "tech_hubtd@example.com", 4),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Gò Vấp - Nguyễn Oanh", "tech_hubgv@example.com", 5),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Bình Thạnh - Ung Văn Khiêm", "tech_hubbth@example.com", 6),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Tân Bình", "tech_hubtb@example.com", 7),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Bình Tân", "tech_hubbt@example.com", 8),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Nhà Bè", "tech_hubnb@example.com", 9),
            (str(uuid.uuid4()), "TECHNICIAN", "Tech Hub Tân Phú", "tech_hubtp@example.com", 10),
            (str(uuid.uuid4()), "CUSTOMER", "Test Customer", "test@test.com", None)
        ]
        
        for uid, rid, name, email, hid in users:
            await conn.execute(text("""
                INSERT INTO users (id, role_id, password_hash, full_name, email, phone, is_active, created_at, hub_id)
                VALUES (:uid, :rid, :pw, :name, :email, '0123456789', true, NOW(), :hid)
            """), {"uid": uid, "rid": rid, "pw": pw_hash, "name": name, "email": email, "hid": hid})

        print("Tạo Drones & Batteries mới...")
        # 3 dòng drone:
        # 1. DJI Matrice 100 (Max payload 1kg). Pin: TB48D (5700mAh)
        # 2. DJI Inspire 2 (Max payload 0.8kg). Pin: TB50 (4280mAh)
        # 3. DJI Matrice 200 V2 (Max payload 1.45kg). Pin: TB55 (7660mAh)
        drones_config = [
            ("DJI Matrice 100", 1.0, 15.0, "TB48D", 5700.0, 2), # 2 drones
            ("DJI Inspire 2", 0.8, 20.0, "TB50", 4280.0, 2), # 2 drones
            ("DJI Matrice 200 V2", 1.45, 18.0, "TB55", 7660.0, 2) # 2 drones
        ]
        
        bat_counter = 1
        for model, cap, speed, bat_model, bat_cap, count in drones_config:
            for i in range(count):
                dname = f"{model} - Unit {i+1}"
                res = await conn.execute(text("""
                    INSERT INTO drones (name, model, payload_capacity_kg, max_speed, operational_status, current_hub_id, battery_level_pct, utilization_pct)
                    VALUES (:name, :model, :cap, :speed, 'Available', 1, 100, 0.0)
                    RETURNING id
                """), {"name": dname, "model": model, "cap": cap, "speed": speed})
                did = res.scalar()
                
                # Tạo Pin tương ứng cho drone
                bat_id = f"BAT-{bat_counter:04d}"
                await conn.execute(text("""
                    INSERT INTO batteries (id, serial_number, capacity_wh, status, drone_id, current_hub_id, charge_level_pct, create_at, battery_model)
                    VALUES (:id, :id, :cap, 'Active', :did, NULL, 100, NOW(), :bmodel)
                """), {"id": bat_id, "cap": bat_cap, "did": did, "bmodel": bat_model})
                bat_counter += 1
                
        # Tạo thêm 5 viên pin dự phòng trong kho (Trạm 1)
        for i in range(5):
            bat_id = f"BAT-{bat_counter:04d}"
            await conn.execute(text("""
                INSERT INTO batteries (id, serial_number, capacity_wh, status, drone_id, current_hub_id, charge_level_pct, create_at, battery_model)
                VALUES (:id, :id, 5700.0, 'Active', NULL, 1, 100, NOW(), 'TB48D')
            """), {"id": bat_id})
            bat_counter += 1

        print("Hoàn tất thiết lập cơ sở dữ liệu mẫu!")

if __name__ == "__main__":
    asyncio.run(seed())
