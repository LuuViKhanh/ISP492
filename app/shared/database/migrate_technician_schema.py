import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from sqlalchemy import text
from app.database.db import engine

def migrate_technician_schema():
    print("🚀 Bắt đầu cập nhật cấu trúc Database cho phân hệ Technician...")
    
    with engine.connect() as conn:
        try:
            # ==========================================
            # 1. TẠO BẢNG HUBS MỚI
            # ==========================================
            print("1. Đang xử lý bảng hubs...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS hubs (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(30) UNIQUE,
                    name VARCHAR(100),
                    address VARCHAR(255),
                    latitude DECIMAL(9,6),
                    longitude DECIMAL(9,6),
                    status VARCHAR(20),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """))

            # ==========================================
            # 2. CẬP NHẬT BẢNG USERS VÀ DRONES
            # ==========================================
            print("2. Đang cập nhật users và drones...")
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS hub_id INT;"))
            
            conn.execute(text("ALTER TABLE drones ADD COLUMN IF NOT EXISTS current_hub_id INT;"))
            conn.execute(text("ALTER TABLE drones ADD COLUMN IF NOT EXISTS battery_level_pct INT;"))
            conn.execute(text("ALTER TABLE drones ADD COLUMN IF NOT EXISTS utilization_pct DECIMAL(5,2);"))
            
            # Đổi tên cột status thành operational_status (bỏ qua nếu đã đổi)
            try:
                conn.execute(text("ALTER TABLE drones RENAME COLUMN status TO operational_status;"))
            except Exception:
                pass # Lỗi thường do cột đã được đổi tên từ trước

            # ==========================================
            # 3. CẬP NHẬT BẢNG MISSIONS VÀ INCIDENTS
            # ==========================================
            print("3. Đang cập nhật missions và incidents...")
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS origin_hub_id INT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS destination_hub_id INT;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS departed_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS arrived_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS arrival_confirmed_by VARCHAR(50);"))
            conn.execute(text("ALTER TABLE missions ADD COLUMN IF NOT EXISTS arrival_confirmed_at TIMESTAMP;"))
            
            conn.execute(text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS requires_technical_inspection BOOLEAN DEFAULT FALSE;"))

            # ==========================================
            # 4. TẠO CÁC BẢNG BẢO TRÌ (MAINTENANCE)
            # ==========================================
            print("4. Đang tạo các bảng schedules và alerts...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS maintenance_schedules (
                    id SERIAL PRIMARY KEY,
                    drone_id INT,
                    maintenance_type VARCHAR(50),
                    interval_days INT,
                    interval_flight_hours DECIMAL,
                    last_inspection_at TIMESTAMP,
                    next_inspection_at TIMESTAMP,
                    status VARCHAR(20),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS maintenance_alerts (
                    id SERIAL PRIMARY KEY,
                    drone_id INT,
                    source VARCHAR(30),
                    maintenance_schedule_id INT,
                    mission_id VARCHAR(50),
                    incident_id INT,
                    title VARCHAR(255),
                    status VARCHAR(20),
                    work_order_id INT,
                    handled_by VARCHAR(50),
                    created_at TIMESTAMP DEFAULT NOW(),
                    handled_at TIMESTAMP
                );
            """))

            # ==========================================
            # 5. CẬP NHẬT / TẠO BẢNG WORK ORDERS
            # ==========================================
            print("5. Đang xử lý work_orders và logs...")
            # Tạo bảng nếu chưa có
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS work_orders (
                    id SERIAL PRIMARY KEY,
                    drone_id INT,
                    battery_id INT,
                    alert_id INT,
                    title VARCHAR(255),
                    priority VARCHAR(20),
                    status VARCHAR(30),
                    created_by VARCHAR(50),
                    assigned_to VARCHAR(50),
                    issue_description TEXT,
                    action_taken TEXT,
                    scheduled_at TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """))
            
            # Cố gắng bổ sung cột nếu bảng đã có từ thiết kế cũ
            try:
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS alert_id INT;"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS title VARCHAR(255);"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS priority VARCHAR(20);"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS created_by VARCHAR(50);"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(50);"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP;"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;"))
                conn.execute(text("ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;"))
            except Exception:
                pass

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS work_order_logs (
                    id SERIAL PRIMARY KEY,
                    work_order_id INT,
                    action VARCHAR(100),
                    from_status VARCHAR(30),
                    to_status VARCHAR(30),
                    changed_by VARCHAR(50),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """))

            # ==========================================
            # 6. TẠO CÁC BẢNG KẾT QUẢ BẢO TRÌ
            # ==========================================
            print("6. Đang tạo bảng maintenance_records và inspection_items...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS maintenance_records (
                    id SERIAL PRIMARY KEY,
                    work_order_id INT UNIQUE,
                    drone_id INT,
                    performed_by VARCHAR(50),
                    title VARCHAR(255),
                    diagnosis TEXT,
                    corrective_action TEXT,
                    resulting_drone_status VARCHAR(30),
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS maintenance_inspection_items (
                    id SERIAL PRIMARY KEY,
                    maintenance_record_id INT,
                    item_name VARCHAR(255),
                    is_completed BOOLEAN DEFAULT FALSE,
                    checked_at TIMESTAMP
                );
            """))

            # ==========================================
            # 7. CẬP NHẬT NOTIFICATIONS
            # ==========================================
            print("7. Đang cập nhật notifications...")
            conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS drone_id INT;"))
            conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS work_order_id INT;"))
            conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS mission_id VARCHAR(50);"))

            # Commit tất cả thay đổi
            conn.commit()
            print("✅ Đã hoàn thành quá trình migrate Database cho phân hệ Technician!")
            
        except Exception as e:
            conn.rollback()
            print(f"❌ Lỗi khi migrate: {e}")

if __name__ == "__main__":
    migrate_technician_schema()
