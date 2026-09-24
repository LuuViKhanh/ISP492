import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from sqlalchemy import text
from app.database.db import engine

def undo_migrate_technician_schema():
    print("⚠️ Bắt đầu UNDO (Hoàn tác) cấu trúc Database phân hệ Technician...")
    
    with engine.connect() as conn:
        try:
            # ==========================================
            # 1. HOÀN TÁC NOTIFICATIONS
            # ==========================================
            print("1. Đang hoàn tác bảng notifications...")
            conn.execute(text("ALTER TABLE notifications DROP COLUMN IF EXISTS drone_id;"))
            conn.execute(text("ALTER TABLE notifications DROP COLUMN IF EXISTS work_order_id;"))
            conn.execute(text("ALTER TABLE notifications DROP COLUMN IF EXISTS mission_id;"))

            # ==========================================
            # 2. XÓA CÁC BẢNG BẢO TRÌ (Xóa cẩn thận bằng CASCADE)
            # ==========================================
            print("2. Đang xóa các bảng bảo trì (records, items, alerts, schedules, logs)...")
            conn.execute(text("DROP TABLE IF EXISTS maintenance_inspection_items CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS maintenance_records CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS work_order_logs CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS maintenance_alerts CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS maintenance_schedules CASCADE;"))
            
            # Đối với bảng work_orders, do có khả năng DB cũ đã có sẵn bảng này nên ta chỉ DROP các cột vừa thêm vào
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS alert_id;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS title;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS priority;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS created_by;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS assigned_to;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS scheduled_at;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS started_at;"))
            conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS completed_at;"))

            # ==========================================
            # 3. HOÀN TÁC MISSIONS & INCIDENTS
            # ==========================================
            print("3. Đang hoàn tác missions và incidents...")
            conn.execute(text("ALTER TABLE missions DROP COLUMN IF EXISTS origin_hub_id;"))
            conn.execute(text("ALTER TABLE missions DROP COLUMN IF EXISTS destination_hub_id;"))
            conn.execute(text("ALTER TABLE missions DROP COLUMN IF EXISTS departed_at;"))
            conn.execute(text("ALTER TABLE missions DROP COLUMN IF EXISTS arrived_at;"))
            conn.execute(text("ALTER TABLE missions DROP COLUMN IF EXISTS arrival_confirmed_by;"))
            conn.execute(text("ALTER TABLE missions DROP COLUMN IF EXISTS arrival_confirmed_at;"))
            
            conn.execute(text("ALTER TABLE incidents DROP COLUMN IF EXISTS requires_technical_inspection;"))

            # ==========================================
            # 4. HOÀN TÁC USERS & DRONES
            # ==========================================
            print("4. Đang hoàn tác users và drones...")
            conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS hub_id;"))
            
            conn.execute(text("ALTER TABLE drones DROP COLUMN IF EXISTS current_hub_id;"))
            conn.execute(text("ALTER TABLE drones DROP COLUMN IF EXISTS battery_level_pct;"))
            conn.execute(text("ALTER TABLE drones DROP COLUMN IF EXISTS utilization_pct;"))
            
            # Khôi phục tên cột operational_status về status
            try:
                conn.execute(text("ALTER TABLE drones RENAME COLUMN operational_status TO status;"))
            except Exception:
                pass

            # ==========================================
            # 5. XÓA BẢNG HUBS (Phải xóa cuối cùng vì có FK)
            # ==========================================
            print("5. Đang xóa bảng hubs...")
            conn.execute(text("DROP TABLE IF EXISTS hubs CASCADE;"))

            # Commit tất cả
            conn.commit()
            print("✅ Đã hoàn thành quá trình UNDO Database! Trở về trạng thái ban đầu.")
            
        except Exception as e:
            conn.rollback()
            print(f"❌ Lỗi khi UNDO: {e}")

if __name__ == "__main__":
    undo_migrate_technician_schema()
