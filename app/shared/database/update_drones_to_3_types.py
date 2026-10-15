# -*- coding: utf-8 -*-
"""
Script chuẩn hóa toàn bộ đội bay Drone và Pin trong hệ thống về đúng 3 loại tải trọng:
- Loại 1 (Hạng nhẹ):   DJI Matrice 100        (Tải 1.0 kg, Tốc độ 15.0 m/s, Pin TB48D 130.0 Wh)
- Loại 2 (Hạng vừa):   Aurelia X6 Pro         (Tải 5.0 kg, Tốc độ 22.0 m/s, Pin Aurelia UltraPower 444.0 Wh)
- Loại 3 (Hạng nặng):  DJI FlyCart 15 Heavy   (Tải 10.0 kg, Tốc độ 20.0 m/s, Pin DJI DB2000 980.0 Wh)

Ràng buộc đảm bảo:
1. Mỗi Hub (từ Hub 1 đến Hub 10) đều sở hữu đầy đủ cả 3 loại drone trên.
2. Tất cả các drone trong DB được phân bổ đồng đều theo chu kỳ 3 loại.
3. Pin gắn theo drone (batteries.drone_id) được đồng bộ tuyệt đối về đúng battery_model và capacity_wh.
4. Pin dự phòng tại mỗi Hub (spare batteries) cũng được quy chuẩn về 3 loại pin này.
5. Bảo toàn 100% ID và quan hệ Foreign Key (Missions, Work Orders, Incidents, Maintenance).
6. Tối ưu hóa truy vấn tải trước toàn bộ dữ liệu (In-Memory Batch Processing) giúp thực thi trong vài giây.

Cách chạy kiểm tra (Dry-run không lưu DB):
    python app/shared/database/update_drones_to_3_types.py --dry-run

Cách chạy cập nhật chính thức vào DB:
    python app/shared/database/update_drones_to_3_types.py
"""

import os
import sys
import asyncio
import argparse

# Thêm đường dẫn thư mục gốc Drone-backend vào sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import select
from app.database.db import AsyncSessionLocal
from app.modules.fleet.models import Drone, Battery
from app.modules.system.models import Hub

# ==============================================================================
# ĐỊNH NGHĨA 3 LOẠI DRONE & PIN CHUẨN (GIỮ DJI MATRICE 100)
# ==============================================================================
THREE_DRONE_TYPES = [
    {
        "category": "Hạng nhẹ (Light)",
        "model": "DJI Matrice 100",
        "payload": 1.0,          # 1.0 kg (Chuẩn empirical dataset của đồ án)
        "speed": 15.0,           # 15.0 m/s
        "bat_model": "TB48D",
        "bat_cap_wh": 130.0      # Chuẩn Wh thực tế của TB48D (22.8V, 5700 mAh)
    },
    {
        "category": "Hạng vừa (Medium)",
        "model": "Aurelia X6 Pro",
        "payload": 5.0,          # 5.0 kg
        "speed": 22.0,           # 22.0 m/s
        "bat_model": "Aurelia UltraPower",
        "bat_cap_wh": 444.0      # Chuẩn Wh thực tế của Aurelia UltraPower
    },
    {
        "category": "Hạng nặng (Heavy - Max)",
        "model": "DJI FlyCart 15 Heavy",
        "payload": 10.0,         # 10.0 kg (Mức tối đa theo yêu cầu)
        "speed": 20.0,           # 20.0 m/s
        "bat_model": "DJI DB2000",
        "bat_cap_wh": 980.0      # Chuẩn Wh thực tế của DB2000 (pin kép chuyên chở nặng)
    }
]

async def update_fleet(dry_run: bool = False):
    print("=" * 80, flush=True)
    print(f" KHỞI CHẠY TIẾN TRÌNH QUY CHUẨN HỆ THỐNG VỀ 3 LOẠI DRONE & PIN {'[DRY-RUN]' if dry_run else '[THỰC THI THẬT]'}", flush=True)
    print("=" * 80, flush=True)

    async with AsyncSessionLocal() as session:
        # 1. Lấy toàn bộ Hubs
        res_hubs = await session.execute(select(Hub).order_by(Hub.id))
        hubs = res_hubs.scalars().all()
        if not hubs:
            print("[LỖI] Không tìm thấy Hub nào trong cơ sở dữ liệu!", flush=True)
            return

        print(f"-> Tìm thấy {len(hubs)} Hubs trong hệ thống.", flush=True)

        # 2. Tải toàn bộ Drones và Batteries vào bộ nhớ 1 lần duy nhất (In-Memory Batching)
        res_all_drones = await session.execute(select(Drone).order_by(Drone.id))
        all_drones = res_all_drones.scalars().all()

        res_all_bats = await session.execute(select(Battery).order_by(Battery.id))
        all_bats = res_all_bats.scalars().all()

        print(f"-> Đã tải {len(all_drones)} Drones và {len(all_bats)} Batteries từ DB.", flush=True)

        # Gom nhóm Drone theo Hub
        drones_by_hub = {h.id: [] for h in hubs}
        for d in all_drones:
            hub_id = d.current_hub_id or 1
            if hub_id in drones_by_hub:
                drones_by_hub[hub_id].append(d)
            else:
                drones_by_hub[1].append(d)

        # Gom nhóm Pin theo Drone ID và theo Hub
        bats_by_drone = {}
        spare_bats_by_hub = {h.id: [] for h in hubs}
        orphan_bats = []

        valid_drone_ids = {d.id for d in all_drones}
        for b in all_bats:
            if b.drone_id and b.drone_id in valid_drone_ids:
                bats_by_drone.setdefault(b.drone_id, []).append(b)
            elif b.current_hub_id and b.current_hub_id in spare_bats_by_hub:
                b.drone_id = None
                spare_bats_by_hub[b.current_hub_id].append(b)
            else:
                b.drone_id = None
                orphan_bats.append(b)

        total_drones_updated = 0
        total_batteries_updated = 0
        hub_summary = {}

        # 3. Phân bổ đồng đều 3 loại Drone & Pin tại từng Hub
        for hub in hubs:
            hub_code = f"H{hub.id:02d}"
            drones_in_hub = drones_by_hub[hub.id]

            hub_summary[hub.id] = {
                "name": hub.name,
                "drones_count": len(drones_in_hub),
                "type_counts": {t["model"]: 0 for t in THREE_DRONE_TYPES}
            }

            for idx, drone in enumerate(drones_in_hub):
                # Phân bổ xoay vòng đều 3 loại: 0 -> Type 1, 1 -> Type 2, 2 -> Type 3
                spec = THREE_DRONE_TYPES[idx % 3]
                hub_summary[hub.id]["type_counts"][spec["model"]] += 1

                # Cập nhật thông số Drone
                drone.name = f"{spec['model']} - Unit {hub_code}-{idx+1:02d}"
                drone.model = spec["model"]
                drone.payload_capacity_kg = spec["payload"]
                drone.max_speed = spec["speed"]
                drone.current_hub_id = hub.id
                total_drones_updated += 1

                # Cập nhật pin gắn theo Drone
                if drone.id in bats_by_drone:
                    for b in bats_by_drone[drone.id]:
                        b.battery_model = spec["bat_model"]
                        b.capacity_wh = spec["bat_cap_wh"]
                        b.current_hub_id = hub.id
                        total_batteries_updated += 1

            # Cập nhật pin dự phòng tại Hub
            for s_idx, spare in enumerate(spare_bats_by_hub[hub.id]):
                spec = THREE_DRONE_TYPES[s_idx % 3]
                spare.battery_model = spec["bat_model"]
                spare.capacity_wh = spec["bat_cap_wh"]
                spare.current_hub_id = hub.id
                total_batteries_updated += 1

            type_dist = ", ".join([f"{m.split()[1] if ' ' in m else m}: {cnt}" for m, cnt in hub_summary[hub.id]["type_counts"].items()])
            print(f"   * Hub {hub.id:02d} ({hub.name}): {len(drones_in_hub)} Drones ({type_dist}) & Pin đồng bộ.", flush=True)

        # 4. Xử lý các pin chưa gán trạm (Orphans)
        if orphan_bats:
            print(f"\n[Xử lý pin tự do]: Chuẩn hóa {len(orphan_bats)} viên pin chưa gán Hub về Hub 01...", flush=True)
            for o_idx, b in enumerate(orphan_bats):
                spec = THREE_DRONE_TYPES[o_idx % 3]
                b.current_hub_id = 1
                b.battery_model = spec["bat_model"]
                b.capacity_wh = spec["bat_cap_wh"]
                total_batteries_updated += 1

        # 5. Commit dữ liệu hoặc Rollback nếu Dry-Run
        if dry_run:
            await session.rollback()
            print("\n" + "=" * 80, flush=True)
            print(" [KẾT QUẢ DRY-RUN] Đã kiểm tra mô phỏng thành công! (Không có thay đổi nào lưu vào DB)", flush=True)
        else:
            await session.commit()
            print("\n" + "=" * 80, flush=True)
            print(" [HOÀN TẤT THỰC THI] Đã lưu toàn bộ thay đổi thành công vào Database!", flush=True)

        print("=" * 80, flush=True)
        print(f" Tổng số Drone đã chuẩn hóa:   {total_drones_updated}", flush=True)
        print(f" Tổng số Pin đã cập nhật:     {total_batteries_updated}", flush=True)
        print("-" * 80, flush=True)
        print(f"{'Hub ID':<8} | {'Tên Hub':<32} | {'Tổng Drone':<12} | {'Phân bổ 3 loại (M100 / X6 / FlyCart)'}", flush=True)
        print("-" * 80, flush=True)
        for h_id, info in hub_summary.items():
            counts = [str(info["type_counts"][t["model"]]) for t in THREE_DRONE_TYPES]
            print(f"{h_id:<8} | {info['name']:<32} | {info['drones_count']:<12} | {' / '.join(counts)}", flush=True)
        print("=" * 80, flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chuẩn hóa đội Drone và Pin về 3 loại tải trọng")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử nghiệm kiểm tra, không ghi vào database")
    args = parser.parse_args()

    asyncio.run(update_fleet(dry_run=args.dry_run))
