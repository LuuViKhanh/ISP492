# -*- coding: utf-8 -*-
r"""
Script chuẩn hóa và sửa đổi dung lượng Pin (capacity_wh) từ đơn vị nhầm lẫn (mAh) về chuẩn kỹ thuật thực tế (Wh - Watt-hour).
- Truy vấn lấy dữ liệu hiện tại từ bảng batteries để kiểm tra.
- Quy đổi và cập nhật dung lượng chuẩn theo thông số kỹ thuật của nhà sản xuất (DJI, Tattu, Foxtech, Aurelia, Freefly).
- Sử dụng AsyncSessionLocal và Transaction an toàn.

Cách chạy:
    python app/shared/database/fix_battery_capacity_wh.py
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
from app.modules.fleet.models import Battery

# Bảng thông số kỹ thuật thực tế chuẩn theo Wh (Watt-hours) của từng dòng pin:
# Công thức: Wh = (mAh * Điện áp V) / 1000
REAL_SPECS_WH = {
    # Pin dòng drone nhẹ / tầm trung (DJI Matrice 100, Inspire 2, Matrice 200)
    "TB48D": 130.0,             # 22.8V, 5700 mAh -> 129.96 Wh (DJI Matrice 100)
    "TB50": 98.0,               # 22.8V, 4280 mAh -> 97.58 Wh (DJI Inspire 2)
    "TB55": 175.0,              # 22.8V, 7660 mAh -> 174.65 Wh (DJI Matrice 200 V2)
    
    # Pin dòng drone công nghiệp tầm trung (Matrice 300, Matrice 350, Loong)
    "DJI TB60": 274.0,          # 46.2V, 5935 mAh -> 274.2 Wh (DJI Matrice 300 RTK)
    "DJI TB65": 263.0,          # 44.76V, 5880 mAh -> 263.2 Wh (DJI Matrice 350 RTK)
    "Foxtech 6S": 160.0,        # 22.2V, 7200 mAh -> 159.8 Wh (Foxtech Loong 2160)
    "Skyfront PowerPlus": 320.0,# 44.4V, 7200 mAh -> 319.7 Wh (Skyfront Perimeter 8)

    # Pin dòng drone tải nặng 3.5kg - 5.0kg (Tarot, Gryphon, Aurelia, Freefly, FlyCart)
    "Tattu Pro 6S": 350.0,      # Hệ 6S Dual / 16000 mAh -> 350 Wh (Tarot T960)
    "Tattu Pro 12S": 380.0,     # 44.4V, 8500 mAh -> 377.4 Wh (Tarot X6)
    "Gryphon Dual 6S": 390.0,   # 44.4V, 8800 mAh -> 390.7 Wh (Gryphon GD-X8)
    "Aurelia UltraPower": 444.0,# 44.4V, 10000 mAh -> 444.0 Wh (Aurelia X6 Pro)
    "Freefly Trekker": 422.0,   # 44.4V, 9500 mAh -> 421.8 Wh (Freefly Alta X)
    "DJI DB2000": 980.0,        # Pin chuyên dụng cho DJI FlyCart 15/30 -> 980 Wh
}

async def update_battery_capacities():
    print("================================================================================")
    print("   BẮT ĐẦU CHUẨN HÓA DUNG LƯỢNG PIN (CAPACITY_WH) VỀ THÔNG SỐ THỰC TẾ")
    print("================================================================================")

    async with AsyncSessionLocal() as db:
        # 1. Lấy dữ liệu hiện tại từ DB để kiểm tra
        print("\n--- BƯỚC 1: KIỂM TRA DỮ LIỆU HIỆN CÓ TRONG BẢNG BATTERIES ---")
        res_current = await db.execute(
            text("SELECT battery_model, capacity_wh, COUNT(*) as count FROM batteries GROUP BY battery_model, capacity_wh ORDER BY count DESC;")
        )
        current_rows = res_current.fetchall()
        print(f"{'Battery Model':<25} | {'Dung lượng cũ (trong DB)':<25} | {'Số lượng pin'}")
        print("-" * 70)
        for row in current_rows:
            model = row[0] or "Chưa có model"
            cap = row[1]
            cnt = row[2]
            print(f"{model:<25} | {cap:<25} | {cnt}")

        # 2. Thực hiện cập nhật theo thông số chuẩn thực tế (Wh)
        print("\n--- BƯỚC 2: TIẾN HÀNH CẬP NHẬT CHUẨN HÓA VỀ ĐƠN VỊ WH THỰC TẾ ---")
        total_updated = 0
        for model_name, correct_wh in REAL_SPECS_WH.items():
            result = await db.execute(
                text("""
                    UPDATE batteries 
                    SET capacity_wh = :new_wh, update_at = NOW()
                    WHERE battery_model = :model_name;
                """),
                {"new_wh": correct_wh, "model_name": model_name}
            )
            count = result.rowcount
            if count > 0:
                print(f" -> Đã cập nhật model [{model_name}]: {correct_wh} Wh ({count} viên pin).")
                total_updated += count

        # Xử lý các pin chưa có model (nếu có) gán mặc định 150 Wh
        res_null = await db.execute(
            text("""
                UPDATE batteries
                SET capacity_wh = 150.0, update_at = NOW()
                WHERE battery_model IS NULL OR battery_model = '';
            """)
        )
        if res_null.rowcount > 0:
            print(f" -> Đã cập nhật {res_null.rowcount} viên pin không rõ model về mức chuẩn 150.0 Wh.")
            total_updated += res_null.rowcount

        # Commit Transaction
        await db.commit()

        # 3. Kiểm tra lại kết quả sau khi cập nhật
        print("\n--- BƯỚC 3: KIỂM TRA KẾT QUẢ SAU KHI CẬP NHẬT ---")
        res_after = await db.execute(
            text("SELECT battery_model, capacity_wh, COUNT(*) as count FROM batteries GROUP BY battery_model, capacity_wh ORDER BY count DESC;")
        )
        after_rows = res_after.fetchall()
        print(f"{'Battery Model':<25} | {'Dung lượng mới (Chuẩn Wh)':<25} | {'Số lượng pin'}")
        print("-" * 70)
        for row in after_rows:
            model = row[0] or "Chưa có model"
            cap = row[1]
            cnt = row[2]
            print(f"{model:<25} | {f'{cap} Wh':<25} | {cnt}")

        print("\n================================================================================")
        print(f" THÀNH CÔNG: Đã chuẩn hóa {total_updated} viên pin về đúng chuẩn thông số thực tế Wh!")
        print(" Hệ thống AI tính toán hao pin, đánh giá rủi ro và điều phối trạm sạc sẽ hoạt động chính xác.")
        print("================================================================================")

if __name__ == "__main__":
    asyncio.run(update_battery_capacities())
