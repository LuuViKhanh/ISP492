import os
import uuid
import pandas as pd
from sqlalchemy import text
from app.database.db import engine

CSV_FILE_PATH = r"D:\works\Đồ án kỳ 2\previous_project\RS\data\processed\cleaned_flight_data.csv"
NUM_FLIGHTS_TO_IMPORT = 10 

def import_rs_data():
    print("🚀 Bắt đầu import dữ liệu từ RS vào Database...")
    
    with engine.connect() as conn:
        # ==========================================
        # 0. TỰ ĐỘNG SỬA LỖI DATABASE SCHEMA
        # ==========================================
        try:
            print("Đang ép PostgreSQL đồng bộ kiểu dữ liệu UUID cho bảng missions...")
            conn.execute(text("ALTER TABLE missions ALTER COLUMN customer_id TYPE VARCHAR;"))
            conn.execute(text("ALTER TABLE missions ALTER COLUMN operator_id TYPE VARCHAR;"))
            conn.commit()
            print("✅ Đã sửa xong cấu trúc Database!")
        except Exception as e:
            conn.rollback()
            print("⚠️ (Bỏ qua nếu DB đã được sửa từ trước)")

        # ==========================================
        # 1. XỬ LÝ ROLES & USERS
        # ==========================================
        customer_role_id = "a1d9a625-d2e9-4dea-b68f-e452cf3120f5"
        role_exists = conn.execute(text("SELECT id FROM roles WHERE id = :id"), {"id": customer_role_id}).scalar()
        if not role_exists:
            print("Khởi tạo Role Customer...")
            conn.execute(text("INSERT INTO roles (id, role_name, description) VALUES (:id, 'Customer', 'Khách hàng')"), {"id": customer_role_id})

        customer_id = conn.execute(text("SELECT id FROM users WHERE email = 'rs_mock@drone.com'")).scalar()
        if not customer_id:
            print("Khởi tạo Mock Customer...")
            customer_id = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO users (id, role_id, username, password_hash, full_name, email, is_active)
                VALUES (:id, :role_id, 'mock_customer', 'mock_hash', 'Mock Customer (RS)', 'rs_mock@drone.com', true)
            """), {"id": customer_id, "role_id": customer_role_id})

        # ==========================================
        # 2. XỬ LÝ DRONES
        # ==========================================
        dji_drone_id = conn.execute(text("SELECT id FROM drones WHERE name = 'DJI Matrice 100'")).scalar()
        if not dji_drone_id:
            print("Thêm Drone DJI...")
            conn.execute(text("INSERT INTO drones (name, model, payload_capacity_kg, max_speed, status) VALUES ('DJI Matrice 100', 'QUADCOPTER', 5.0, 30.0, 'Available')"))
            dji_drone_id = conn.execute(text("SELECT id FROM drones WHERE name = 'DJI Matrice 100'")).scalar()

        vtol_drone_id = conn.execute(text("SELECT id FROM drones WHERE name = 'MakeFlyEasy Fighter VTOL'")).scalar()
        if not vtol_drone_id:
            print("Thêm Drone VTOL...")
            conn.execute(text("INSERT INTO drones (name, model, payload_capacity_kg, max_speed, status) VALUES ('MakeFlyEasy Fighter VTOL', 'VTOL_FIXED_WING', 5.0, 30.0, 'Available')"))
            vtol_drone_id = conn.execute(text("SELECT id FROM drones WHERE name = 'MakeFlyEasy Fighter VTOL'")).scalar()

        # ==========================================
        # 3. XỬ LÝ LOCATIONS
        # ==========================================
        hub_id = conn.execute(text("SELECT id FROM locations WHERE name = 'Hub Penn Hills'")).scalar()
        if not hub_id:
            print("Thêm Location Hub Penn Hills...")
            conn.execute(text("INSERT INTO locations (name, latitude, longitude, type) VALUES ('Hub Penn Hills', 40.465690, -79.788281, 'Hub')"))
            hub_id = conn.execute(text("SELECT id FROM locations WHERE name = 'Hub Penn Hills'")).scalar()

        hub_nardo_id = conn.execute(text("SELECT id FROM locations WHERE name = 'Hub Nardo Flight Test Field'")).scalar()
        if not hub_nardo_id:
            print("Thêm Location Hub Nardo...")
            conn.execute(text("INSERT INTO locations (name, latitude, longitude, type) VALUES ('Hub Nardo Flight Test Field', 40.5834006, -79.8997747, 'Hub')"))

        # ==========================================
        # 4. XỬ LÝ ĐỌC DATA TỪ CSV
        # ==========================================
        if not os.path.exists(CSV_FILE_PATH):
            print(f"❌ Không tìm thấy file CSV tại {CSV_FILE_PATH}")
            return

        print(f"Đọc dữ liệu từ {CSV_FILE_PATH}...")
        df = pd.read_csv(CSV_FILE_PATH)
        
        unique_flights = df['flight'].unique()[:NUM_FLIGHTS_TO_IMPORT]
        print(f"Tiến hành import {len(unique_flights)} chuyến bay...")
        
        for flight_id in unique_flights:
            flight_data = df[df['flight'] == flight_id].copy()
            if flight_data.empty: continue
                
            first_row = flight_data.iloc[0]
            start_time_str = f"{first_row['date']} {first_row['time_day']}:00"
            payload_weight = float(first_row['payload']) / 1000.0
            
            existing_mission = conn.execute(text("SELECT id FROM missions WHERE start_time = :start_time AND payload_weight = :payload"), 
                                         {"start_time": start_time_str, "payload": payload_weight}).scalar()
            
            if not existing_mission:
                mission_res = conn.execute(text("""
                    INSERT INTO missions (customer_id, drone_id, pickup_location_id, dropoff_location_id, payload_weight, distance_m, delivery_fee, status, scheduled_time, start_time)
                    VALUES (:customer_id, :drone_id, :pickup_id, :dropoff_id, :payload, 0.0, 150000, 'Completed', :start_time, :start_time)
                    RETURNING id
                """), {
                    "customer_id": customer_id,   # TRUYỀN ID CHUẨN XÁC
                    "drone_id": dji_drone_id,
                    "pickup_id": hub_id,
                    "dropoff_id": hub_id,
                    "payload": payload_weight,
                    "start_time": start_time_str
                })
                
                new_mission_id = mission_res.scalar()
                
                telemetry_records = []
                for _, row in flight_data.iterrows():
                    telemetry_records.append({
                        "mission_id": new_mission_id,
                        "timestamp": start_time_str, 
                        "latitude": float(row['position_y']),
                        "longitude": float(row['position_x']),
                        "altitude": float(row.get('position_z', row.get('altitude', 0))),
                        "speed": float(row['speed']),
                        "battery_voltage": float(row.get('battery_voltage', 0)),
                        "wind_speed": float(row.get('wind_speed', 0))
                    })
                
                if telemetry_records:
                    conn.execute(text("""
                        INSERT INTO telemetry_logs (mission_id, timestamp, latitude, longitude, altitude, speed, battery_voltage, wind_speed)
                        VALUES (:mission_id, :timestamp, :latitude, :longitude, :altitude, :speed, :battery_voltage, :wind_speed)
                    """), telemetry_records)
                    
                print(f"✅ Đã import xong chuyến bay {flight_id} với {len(telemetry_records)} điểm tọa độ.")
            else:
                print(f"⚠️ Chuyến bay {flight_id} đã tồn tại trong DB, bỏ qua import.")
            
        conn.commit()
        print("🎉 Chúc mừng! Cập nhật dữ liệu từ RS thành công!")

if __name__ == "__main__":
    import_rs_data()