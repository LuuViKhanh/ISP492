import os
from sqlalchemy import text
from app.database.db import engine

def undo_rs_data():
    print("🧹 Bắt đầu quá trình DỌN DẸP dữ liệu RS khỏi Database...")
    
    with engine.connect() as conn:
        try:
            # Lấy ID của Mock Customer
            customer_id = conn.execute(text("SELECT id FROM users WHERE email = 'rs_mock@drone.com'")).scalar()
            
            if customer_id:
                # 1. Xóa Telemetry Logs của các chuyến bay thuộc Mock Customer này
                print("Đang xóa dữ liệu Telemetry_logs (~12.000 dòng)...")
                conn.execute(text("""
                    DELETE FROM telemetry_logs 
                    WHERE mission_id IN (
                        SELECT id FROM missions WHERE customer_id = :customer_id
                    )
                """), {"customer_id": customer_id})

                # 2. Xóa các Chuyến bay (Missions)
                print("Đang xóa dữ liệu Missions...")
                conn.execute(text("""
                    DELETE FROM missions WHERE customer_id = :customer_id
                """), {"customer_id": customer_id})
                
                # 3. Xóa Mock Customer
                print("Đang xóa tài khoản Mock Customer...")
                conn.execute(text("DELETE FROM users WHERE id = :customer_id"), {"customer_id": customer_id})
            else:
                print("⚠️ Không tìm thấy Mock Customer, có thể bạn đã xóa từ trước.")

            # 4. Tìm và Xóa Pin (Batteries) & Drones của RS
            dji_drone_id = conn.execute(text("SELECT id FROM drones WHERE name = 'DJI Matrice 100'")).scalar()
            vtol_drone_id = conn.execute(text("SELECT id FROM drones WHERE name = 'MakeFlyEasy Fighter VTOL'")).scalar()
            drone_ids = [i for i in [dji_drone_id, vtol_drone_id] if i is not None]

            if drone_ids:
                print(f"Đang kiểm tra và xóa Pin của các Drone RS...")
                conn.execute(text("DELETE FROM batteries WHERE drone_id = ANY(:drone_ids)"), {"drone_ids": drone_ids})

                print("Đang xóa Drone DJI và VTOL...")
                conn.execute(text("DELETE FROM drones WHERE id = ANY(:drone_ids)"), {"drone_ids": drone_ids})

            # 5. Tìm và Xóa Locations (Hubs) của RS
            hub_penn_id = conn.execute(text("SELECT id FROM locations WHERE name = 'Hub Penn Hills'")).scalar()
            hub_nardo_id = conn.execute(text("SELECT id FROM locations WHERE name = 'Hub Nardo Flight Test Field'")).scalar()
            location_ids = [i for i in [hub_penn_id, hub_nardo_id] if i is not None]

            if location_ids:
                print("Đang xóa Locations (Hub Penn Hills & Hub Nardo)...")
                conn.execute(text("DELETE FROM locations WHERE id = ANY(:location_ids)"), {"location_ids": location_ids})

            conn.commit()
            print("✅ Đã dọn dẹp sạch sẽ 100% dữ liệu mẫu từ dự án RS. Database đã trở lại trạng thái ban đầu!")

        except Exception as e:
            conn.rollback()
            print(f"❌ Có lỗi xảy ra trong quá trình xóa dữ liệu (Đã hoàn tác thay đổi): {e}")

if __name__ == "__main__":
    undo_rs_data()