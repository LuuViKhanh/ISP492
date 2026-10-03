import sys
import os
import random
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from sqlalchemy import text
from app.database.db import engine

def seed():
    print("Bắt đầu nạp và SỬA CHỮA dữ liệu Mock (Upsert)...")

    hubs_data = [
        (3, "HUB-Q1", "Hub Quận 1 - Bến Thành", "1 Lê Lai, Quận 1, TP.HCM", 10.772310, 106.698270),
        (4, "HUB-Q3", "Hub Quận 3 - Võ Văn Tần", "125 Võ Văn Tần, Quận 3, TP.HCM", 10.777220, 106.686770),
        (5, "HUB-Q7", "Hub Quận 7 - Phú Mỹ Hưng", "Đại lộ Nguyễn Văn Linh, Quận 7, TP.HCM", 10.729410, 106.718390),
        (6, "HUB-BTH", "Hub Bình Thạnh - Ung Văn Khiêm", "100 Ung Văn Khiêm, Bình Thạnh, TP.HCM", 10.812260, 106.711020),
        (7, "HUB-TP", "Hub Tân Phú - Lũy Bán Bích", "300 Lũy Bán Bích, Tân Phú, TP.HCM", 10.790020, 106.628010),
        (8, "HUB-GV", "Hub Gò Vấp - Nguyễn Oanh", "200 Nguyễn Oanh, Gò Vấp, TP.HCM", 10.838120, 106.675310),
        (9, "HUB-TB", "Hub Tân Bình - Cộng Hòa", "15 Cộng Hòa, Tân Bình, TP.HCM", 10.801430, 106.652190),
        (10, "HUB-BT", "Hub Bình Tân - Kinh Dương", "450 Kinh Dương Vương, Bình Tân, TP.HCM", 10.745530, 106.613420),
        (11, "HUB-NB", "Hub Nhà Bè - Huỳnh Tấn Phát", "1000 Huỳnh Tấn Phát, Nhà Bè, TP.HCM", 10.682040, 106.735410),
        (12, "HUB-TD", "Hub Thủ Đức - Võ Văn Ngân", "371 Võ Văn Ngân, Thủ Đức, TP.HCM", 10.850120, 106.772310)
    ]

    with engine.connect() as conn:
        with conn.begin():
            print("1. Hubs (Sửa dữ liệu cũ nếu sai lệch)...")
            for h_id, code, name, address, lat, lng in hubs_data:
                conn.execute(text("""
                    INSERT INTO hubs (id, code, name, address, latitude, longitude, status, created_at, updated_at)
                    VALUES (:id, :code, :name, :address, :lat, :lng, 'Active', NOW(), NOW())
                    ON CONFLICT (id) DO UPDATE SET 
                        code = EXCLUDED.code, 
                        name = EXCLUDED.name, 
                        address = EXCLUDED.address,
                        latitude = EXCLUDED.latitude, 
                        longitude = EXCLUDED.longitude,
                        status = 'Active'
                """), {"id": h_id, "code": code, "name": name, "address": address, "lat": lat, "lng": lng})

            print("2. Users (Sửa chữa thông tin thợ máy nếu khuyết)...")
            # Tìm ID thực sự của role TECHNICIAN thay vì hardcode 'TECHNICIAN'
            tech_role_id = conn.execute(text("SELECT id FROM roles WHERE UPPER(role_name) = 'TECHNICIAN' LIMIT 1")).scalar()
            if not tech_role_id:
                print("   -> Cảnh báo: Không tìm thấy role TECHNICIAN trong bảng roles, đang tạo mới...")
                tech_role_id = "role_tech_mock"
                conn.execute(text("INSERT INTO roles (id, role_name, description) VALUES (:rid, 'TECHNICIAN', 'Thợ máy') ON CONFLICT DO NOTHING"), {"rid": tech_role_id})
            
            conn.execute(text("UPDATE users SET hub_id = 3 WHERE email = 'tech@example.com'"))
            
            for h_id, code, name, address, lat, lng in hubs_data:
                if h_id == 3: continue
                email = f"tech_{code.lower().replace('-', '')}@example.com"
                username = f"tech_{code.lower()}"
                
                conn.execute(text("""
                    INSERT INTO users (id, role_id, password_hash, full_name, email, is_active, created_at, hub_id)
                    VALUES (:uid, :role_id, 'Password123!', :fname, :email, true, NOW(), :hid)
                    ON CONFLICT (email) DO UPDATE SET 
                        role_id = EXCLUDED.role_id,
                        full_name = EXCLUDED.full_name,
                        hub_id = EXCLUDED.hub_id,
                        is_active = true
                """), {"uid": username, "fname": f"Tech {name}", "email": email, "hid": h_id, "role_id": tech_role_id})

            print("3. Drones (Cập nhật thông số/vị trí nếu drone đã tồn tại)...")
            drones_config = [
                ("DJI Mavic 3", 1.5, 15.0, [(3, "Available"), (3, "Available"), (4, "Available"), (4, "In Flight"), (6, "Available")]),
                ("DJI Matrice 300", 5.0, 23.0, [(12, "Available"), (12, "Available"), (5, "Available"), (5, "Available"), (9, "Available"), (8, "Available"), (8, "Maintenance")]),
                ("DJI FlyCart 30", 15.0, 25.0, [(7, "Available"), (10, "In Flight"), (11, "Available")])
            ]
            drone_ids = []
            for model, cap, speed, assignments in drones_config:
                for idx, (h_id, status) in enumerate(assignments):
                    d_name = f"{model} - {h_id} - {idx}"
                    
                    exist_id = conn.execute(text("SELECT id FROM drones WHERE name = :name LIMIT 1"), {"name": d_name}).scalar()
                    if exist_id:
                        conn.execute(text("""
                            UPDATE drones SET model=:model, payload_capacity_kg=:cap, max_speed=:speed, 
                            operational_status=:status, current_hub_id=:hid, battery_level_pct=100
                            WHERE id=:id
                        """), {"id": exist_id, "model": model, "cap": cap, "speed": speed, "status": status, "hid": h_id})
                        drone_ids.append((exist_id, h_id, status))
                    else:
                        res = conn.execute(text("""
                            INSERT INTO drones (name, model, payload_capacity_kg, max_speed, operational_status, created_at, current_hub_id, battery_level_pct, utilization_pct)
                            VALUES (:name, :model, :cap, :speed, :status, NOW(), :hid, 100, 0.0)
                            RETURNING id
                        """), {"name": d_name, "model": model, "cap": cap, "speed": speed, "status": status, "hid": h_id})
                        drone_ids.append((res.scalar(), h_id, status))

            print("4. Batteries (Khôi phục dung lượng pin & gán lại đúng vị trí)...")
            dummy_batt_id = conn.execute(text("""
                INSERT INTO batteries (serial_number, capacity_wh, status, drone_id, current_hub_id, charge_level_pct, create_at, battery_model)
                VALUES ('BATT-DUMMY', 150.0, 'Active', NULL, 3, 100, NOW(), 'Li-Po 6000mAh')
                ON CONFLICT (serial_number) DO UPDATE SET capacity_wh = 150.0, charge_level_pct=100 RETURNING id
            """)).scalar()
            
            for i, (d_id, h_id, status) in enumerate(drone_ids):
                sn = f"BATT-DRONE-{i:03d}"
                conn.execute(text("""
                    INSERT INTO batteries (serial_number, capacity_wh, status, drone_id, current_hub_id, charge_level_pct, create_at, battery_model)
                    VALUES (:sn, 150.0, 'Active', :did, NULL, :charge, NOW(), 'Li-Po 6000mAh')
                    ON CONFLICT (serial_number) DO UPDATE SET 
                        drone_id = EXCLUDED.drone_id, current_hub_id = NULL, capacity_wh = 150.0
                """), {"sn": sn, "did": d_id, "charge": random.randint(20, 100) if status != "Available" else 100})
            
            for i in range(25):
                sn = f"BATT-HUB-{i:03d}"
                h_id = hubs_data[random.randint(0, 9)][0]
                conn.execute(text("""
                    INSERT INTO batteries (serial_number, capacity_wh, status, drone_id, current_hub_id, charge_level_pct, create_at, battery_model)
                    VALUES (:sn, 150.0, 'Active', NULL, :hid, :charge, NOW(), 'Li-Po 6000mAh')
                    ON CONFLICT (serial_number) DO UPDATE SET 
                        drone_id = NULL, current_hub_id = EXCLUDED.current_hub_id
                """), {"sn": sn, "hid": h_id, "charge": random.randint(15, 100)})

            print("5. Orders (Chữa lỗi Missing Data)...")
            for i in range(1, 11):
                o_id = f"ORD-MOCK-PENDING-{i:03d}"
                h1, h2 = random.sample([h[0] for h in hubs_data], 2)
                conn.execute(text("""
                    INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, status, created_at)
                    VALUES (:oid, 'test@test.com', 'Hàng tiêu dùng', 'Standard', :kg, :h1, :h2, 'PENDING', NOW())
                    ON CONFLICT (id) DO UPDATE SET status = 'PENDING', origin_hub_id = EXCLUDED.origin_hub_id, destination_hub_id = EXCLUDED.destination_hub_id
                """), {"oid": o_id, "kg": random.uniform(0.5, 4.5), "h1": h1, "h2": h2})

            print("=> Hoàn tất nạp dữ liệu Mock bằng phương pháp Upsert an toàn!")

if __name__ == '__main__':
    seed()
