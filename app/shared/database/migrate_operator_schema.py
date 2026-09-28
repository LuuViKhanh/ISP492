import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from sqlalchemy import text
from app.database.db import engine

def migrate_operator_schema():
    print("🚀 Bắt đầu cập nhật cấu trúc Database cho phân hệ Operator (Spec VI)...")
    
    with engine.connect() as conn:
        try:
            # ==========================================
            # 1. TẠO BẢNG ORDERS MỚI
            # ==========================================
            print("1. Đang tạo bảng orders...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS orders (
                    id VARCHAR(50) PRIMARY KEY,
                    customer_id VARCHAR(50),
                    package_label VARCHAR(255),
                    package_type VARCHAR(100),
                    payload_kg DECIMAL(5,2),
                    origin_hub_id INT,
                    destination_hub_id INT,
                    delivery_mode VARCHAR(50),
                    requested_delivery_at TIMESTAMP,
                    estimated_window_start TIMESTAMP,
                    estimated_window_end TIMESTAMP,
                    planning_at TIMESTAMP,
                    status VARCHAR(50),
                    origin_received_at TIMESTAMP,
                    origin_received_by VARCHAR(50),
                    destination_received_at TIMESTAMP,
                    destination_received_by VARCHAR(50),
                    replan_required_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    cancelled_at TIMESTAMP
                );
            """))

            # ==========================================
            # 2. CẬP NHẬT BẢNG MISSIONS
            # ==========================================
            print("2. Đang cập nhật bảng missions...")
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS order_id VARCHAR(50);"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS route_id VARCHAR(50);"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS planned_start_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS package_receive_deadline_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS estimated_arrival_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS actual_started_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS actual_completed_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS predicted_duration_min INT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS estimated_energy_wh DECIMAL(6,2);"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS battery_consumption_pct INT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS predicted_remaining_battery_pct INT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS confidence_pct INT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS risk_level VARCHAR(50);"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS created_by VARCHAR(50);"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS cancel_reason TEXT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS failure_reason TEXT;"))

            # ==========================================
            # 3. TẠO BẢNG MISSION_LEGS (Relay Hub Routing)
            # ==========================================
            print("3. Đang tạo bảng mission_legs...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mission_legs (
                    id SERIAL PRIMARY KEY,
                    mission_id INT,
                    sequence_no INT,
                    from_hub_id INT,
                    to_hub_id INT,
                    status VARCHAR(50),
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """))

            # ==========================================
            # 4. TẠO BẢNG MISSION_DRONE_ASSIGNMENT_HISTORY
            # ==========================================
            print("4. Đang tạo bảng mission_drone_assignment_history...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mission_drone_assignment_history (
                    id SERIAL PRIMARY KEY,
                    mission_id INT,
                    old_drone_id INT,
                    new_drone_id INT,
                    reason TEXT,
                    replaced_by VARCHAR(50),
                    replaced_at TIMESTAMP DEFAULT NOW()
                );
            """))

            # Commit tất cả thay đổi
            conn.commit()
            print("✅ Đã hoàn thành quá trình migrate Database cho phân hệ Operator!")
            
        except Exception as e:
            conn.rollback()
            print(f"❌ Lỗi khi migrate: {e}")

if __name__ == "__main__":
    migrate_operator_schema()
