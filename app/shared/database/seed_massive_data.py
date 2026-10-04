import sys
import os
import uuid
import random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from sqlalchemy import text
from app.database.db import engine
from app.modules.auth.service import hash_password

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def seed_massive_data():
    print("Start seeding massive data...")
    
    with engine.connect() as conn:
        with conn.begin():
            # 1. Hashing password
            default_pwd = hash_password("Password123!")

            # 2. Roles
            print("Ensuring Roles exist...")
            roles = [
                ('role_admin', 'ADMIN', 'System Administrator'),
                ('role_operator', 'OPERATOR', 'Flight Operator'),
                ('role_technician', 'TECHNICIAN', 'Maintenance Technician'),
                ('role_customer', 'CUSTOMER', 'End Customer')
            ]
            for r_id, r_name, r_desc in roles:
                conn.execute(text("INSERT INTO roles (id, role_name, description) VALUES (:id, :name, :desc) ON CONFLICT (role_name) DO NOTHING"), {"id": r_id, "name": r_name, "desc": r_desc})

            # 3. Hubs
            print("Creating 5 Hubs...")
            hubs = []
            for i in range(1, 6):
                hub_code = f"HUB-{i:03d}"
                conn.execute(text("""
                    INSERT INTO hubs (id, code, name, address, latitude, longitude, status, created_at, updated_at) 
                    VALUES (:id, :code, :name, :address, :lat, :lon, 'Active', NOW(), NOW())
                    ON CONFLICT (id) DO NOTHING
                """), {
                    "id": i, "code": hub_code, "name": f"Trạm {i}", 
                    "address": f"Address {i}", 
                    "lat": 10.762622 + random.uniform(-0.05, 0.05),
                    "lon": 106.660172 + random.uniform(-0.05, 0.05)
                })
                hubs.append(i)

            # 4. Users
            print("Creating Users...")
            user_data = [
                (str(uuid.uuid4()), 'role_operator', 'Operator One', 'operator1', 'operator@droneoptai.com', None),
                (str(uuid.uuid4()), 'role_technician', 'tech', 'tech', 'tech@example.com', 1),
                (str(uuid.uuid4()), 'role_admin', 'System Administrator', 'admin', 'admin@drone.com', None),
                (str(uuid.uuid4()), 'role_customer', 'Test Customer', 'test_customer', 'test@test.com', None)
            ]
            for uid, r_id, fname, uname, email, h_id in user_data:
                conn.execute(text("""
                    INSERT INTO users (id, role_id, password_hash, full_name, email, is_active, created_at, hub_id, username) 
                    VALUES (:id, :role_id, :pwd, :fname, :email, true, NOW(), :hub_id, :uname)
                    ON CONFLICT (email) DO UPDATE SET hub_id = EXCLUDED.hub_id
                """), {"id": uid, "role_id": r_id, "pwd": default_pwd, "fname": fname, "email": email, "hub_id": h_id, "uname": uname})

            for i in range(2, 6):
                email = f"tech{i}@example.com"
                conn.execute(text("""
                    INSERT INTO users (id, role_id, password_hash, full_name, email, is_active, created_at, hub_id, username) 
                    VALUES (:id, :role_id, :pwd, :fname, :email, true, NOW(), :hub_id, :uname)
                    ON CONFLICT (email) DO NOTHING
                """), {"id": str(uuid.uuid4()), "role_id": 'role_technician', "pwd": default_pwd, "fname": f"Tech Hub {i}", "email": email, "hub_id": i, "uname": f"tech{i}"})

            res = conn.execute(text("SELECT id FROM users WHERE email='test@test.com'"))
            cust_id = res.scalar()
            res = conn.execute(text("SELECT id FROM users WHERE email='operator@droneoptai.com'"))
            op_id = res.scalar()
            res = conn.execute(text("SELECT id FROM users WHERE role_id='role_technician'"))
            tech_ids = [row[0] for row in res.fetchall()]

            # 5. Drones
            print("Creating Drones...")
            drone_ids = []
            for h in hubs:
                for d in range(10):
                    res = conn.execute(text("""
                        INSERT INTO drones (name, model, payload_capacity_kg, max_speed, operational_status, created_at, current_hub_id, battery_level_pct, utilization_pct)
                        VALUES (:name, :model, :payload, :speed, 'Available', NOW(), :hub_id, :bat, :util)
                        RETURNING id
                    """), {
                        "name": f"Drone {h}-{d}", "model": random.choice(["DJI Matrice 300 RTK", "DJI FlyCart 30", "DJI Mavic 3"]),
                        "payload": random.choice([2.0, 5.0, 10.0, 30.0]), "speed": random.uniform(15.0, 25.0),
                        "hub_id": h, "bat": random.randint(50, 100), "util": random.uniform(10.0, 80.0)
                    })
                    drone_ids.append(res.scalar())

            # 6. Batteries
            print("Creating Batteries...")
            battery_ids = []
            for did in drone_ids:
                res = conn.execute(text("""
                    INSERT INTO batteries (drone_id, serial_number, capacity_wh, status, current_hub_id, charge_level_pct, create_at, update_at, battery_model)
                    VALUES (:did, :sn, :cap, 'Active', NULL, :charge, NOW(), NOW(), :model)
                    RETURNING id
                """), {"did": did, "sn": f"BAT-IN-{did}", "cap": random.choice([150.0, 300.0, 500.0]), "charge": random.randint(50, 100), "model": "TB30"})
                battery_ids.append(res.scalar())
            
            for h in hubs:
                for b in range(20):
                    res = conn.execute(text("""
                        INSERT INTO batteries (drone_id, serial_number, capacity_wh, status, current_hub_id, charge_level_pct, create_at, update_at, battery_model)
                        VALUES (NULL, :sn, :cap, 'Active', :hub, :charge, NOW(), NOW(), :model)
                        RETURNING id
                    """), {"sn": f"BAT-SPARE-{h}-{b}-{uuid.uuid4().hex[:4]}", "cap": 300.0, "hub": h, "charge": random.randint(10, 100), "model": "TB30"})

            # 7. Locations
            print("Creating Locations...")
            locations = []
            for l in range(20):
                res = conn.execute(text("""
                    INSERT INTO locations (name, latitude, longitude, type)
                    VALUES (:name, :lat, :lon, 'CustomerAddress')
                    RETURNING id
                """), {"name": f"Location {l}", "lat": 10.7 + random.uniform(0, 0.1), "lon": 106.6 + random.uniform(0, 0.1)})
                locations.append(res.scalar())

            # 8. Orders & Missions
            print("Creating Orders & Missions...")
            # We need valid mission_id for incidents
            mission_ids = []
            for i in range(30):
                order_id = f"ORD-{uuid.uuid4().hex[:6].upper()}"
                orig_hub = random.choice(hubs)
                dest_hub = random.choice([h for h in hubs if h != orig_hub])
                
                conn.execute(text("""
                    INSERT INTO orders (id, customer_id, package_label, package_type, payload_kg, origin_hub_id, destination_hub_id, delivery_mode, status, created_at)
                    VALUES (:id, :cust, :pkg, 'Standard', :wt, :orig, :dest, 'SCHEDULED', 'DELIVERED_TO_HUB', NOW())
                """), {"id": order_id, "cust": cust_id, "pkg": f"Package {i}", "wt": random.uniform(0.5, 4.5), "orig": orig_hub, "dest": dest_hub})
                
                d_id = random.choice(drone_ids)
                b_id = random.choice(battery_ids)
                res = conn.execute(text("""
                    INSERT INTO missions (customer_id, operator_id, drone_id, battery_id, pickup_location_id, dropoff_location_id, payload_weight, distance_m, delivery_fee, status, scheduled_time, start_time, end_time, origin_hub_id, destination_hub_id, order_id, order_code, route_id)
                    VALUES (:cust, :op, :did, :bid, NULL, NULL, :wt, :dist, :fee, 'Completed', NOW() - INTERVAL '2 days', NOW() - INTERVAL '1 day', NOW() - INTERVAL '23 hours', :orig, :dest, :oid, :ocode, 'R-MOCK')
                    RETURNING id
                """), {"cust": cust_id, "op": op_id, "did": d_id, "bid": b_id, "wt": 2.0, "dist": random.uniform(5000, 15000), "fee": random.uniform(50000, 200000), "orig": orig_hub, "dest": dest_hub, "oid": order_id, "ocode": order_id})
                m_id = res.scalar()
                mission_ids.append(m_id)
                
                conn.execute(text("""
                    INSERT INTO payments (id, mission_id, amount, payment_method, status, created_at, paid_at)
                    VALUES (:id, :mid, :amt, 'VNPay', 'Success', NOW(), NOW())
                """), {"id": f"PAY-{m_id}", "mid": m_id, "amt": 100000})

                for t in range(5):
                    conn.execute(text("""
                        INSERT INTO telemetry_logs (mission_id, timestamp, latitude, longitude, altitude, speed)
                        VALUES (:mid, NOW(), :lat, :lon, 50.0, 15.0)
                    """), {"mid": m_id, "lat": 10.7+t*0.01, "lon": 106.6+t*0.01})
                
                conn.execute(text("""
                    INSERT INTO ai_predictions (mission_id, estimated_energy_wh, estimated_duration_m, risk_level, predicted_at)
                    VALUES (:mid, 120.5, 25.0, 'Low', NOW())
                """), {"mid": m_id})

            # 9. Incidents & Work Orders
            print("Creating Incidents & Work Orders...")
            for i in range(15):
                d_id = random.choice(drone_ids)
                tech_id = random.choice(tech_ids)
                m_id = random.choice(mission_ids)
                res = conn.execute(text("""
                    INSERT INTO incidents (mission_id, drone_id, reporter_id, severity, description, status, reported_at)
                    VALUES (:mid, :did, 'System', 'Medium', 'Battery Temp Warning', 'Closed', NOW())
                    RETURNING id
                """), {"mid": m_id, "did": d_id})
                inc_id = res.scalar()
                
                conn.execute(text("""
                    INSERT INTO work_orders (drone_id, battery_id, technician_id, issue_description, action_taken, status, resolved_at, title, priority, created_by)
                    VALUES (:did, 1, :tech, 'Check battery', 'Replaced part', 'Completed', NOW(), 'Periodic Maintenance', 'High', 'System')
                """), {"did": d_id, "tech": tech_id})
                
            print("Massive Seed Successful!")
            
if __name__ == '__main__':
    seed_massive_data()
