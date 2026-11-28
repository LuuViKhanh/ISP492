# -*- coding: utf-8 -*-
"""
seed_incident_benthanh.py
─────────────────────────
Tạo ngay 1 mission đang bị sự cố hư linh kiện & rớt sát Ben Thanh Hub (#1)
(tuyến mặc định: Saigon Hub #2 -> Ben Thanh Hub #1) để demo Live Tracking và
luồng Technician thu hồi + tạo Work Order thay linh kiện tại Hub Bến Thành.

Cách sử dụng:
    python -u app/shared/database/seed_incident_benthanh.py
    python -u app/shared/database/seed_incident_benthanh.py --origin-hub 3 --dest-hub 1
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

_wd = threading.Timer(
    30.0,
    lambda: (print("\n[WATCHDOG_TIMEOUT] Quá thời gian chờ kết nối DB.", flush=True), os._exit(2)),
)
_wd.daemon = True
_wd.start()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from app.database.db import engine
from app.shared.database.reset_db_for_defense import _seed_demo_incident_mission


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo ngay 1 mission incident rớt gần Ben Thanh Hub (#1).")
    parser.add_argument("--origin-hub", type=int, default=2, help="Hub xuất phát (mặc định: 2 - Saigon Hub)")
    parser.add_argument("--dest-hub", type=int, default=1, help="Hub đích / thu hồi (mặc định: 1 - Ben Thanh Hub)")
    args = parser.parse_args()

    t0 = time.perf_counter()
    print(f"[{time.perf_counter()-t0:.2f}s] Đang tạo mission incident cho Hub #{args.dest_hub}...", flush=True)
    try:
        with engine.begin() as conn:
            info = _seed_demo_incident_mission(
                conn,
                origin_hub_id=args.origin_hub,
                dest_hub_id=args.dest_hub,
            )
        print(
            f"\n[{time.perf_counter()-t0:.2f}s] TẠO INCIDENT THÀNH CÔNG!"
            f"\n  - Order / Mission : {info['order_id']} / {info['mission_id']} (Incident #{info['incident_id']})"
            f"\n  - Tuyến bay       : {info['origin_hub']} -> {info['dest_hub']}"
            f"\n  - GPS rơi (85%)   : {info['crash_gps']}"
            f"\n  - Thiết bị khóa   : Drone {info['drone_id']} (Maintenance) + Pin {info['battery_id']}"
            f"\n  - Tech xử lý      : {info['dest_tech_email']} (/incoming -> Confirm Arrival -> Work Order)",
            flush=True,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
