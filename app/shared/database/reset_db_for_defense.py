# -*- coding: utf-8 -*-
"""
reset_db_for_defense.py
───────────────────────
Script đưa cơ sở dữ liệu DroneOptAI (25 bảng chuẩn) về trạng thái SẠCH & TỐT NHẤT
để sẵn sàng demo trước hội đồng bảo vệ đồ án.

Nguyên tắc thiết kế:
  1. GIỮ NGUYÊN 100% hạ tầng & danh mục chuẩn (Master Data):
     hubs, accounts, battery_model_catalog, part_categories, hub_supplies, maintenance_schedules.
  2. GIỮ NGUYÊN toàn bộ đội bay (drones) & pin (batteries) đã chuẩn hóa mã, nhưng RESET
     trạng thái về 100% sẵn sàng bay (Available / Active, pin 100%, tháo pin khỏi drone/WO,
     giờ bay = 0, không có drone/pin mồ côi khỏi Hub).
  3. XÓA SẠCH toàn bộ dữ liệu nghiệp vụ phát sinh & nhật ký (17 bảng transactional/logs)
     theo đúng thứ tự khóa ngoại (FK), đồng thời reset các SERIAL/IDENTITY sequence về 1.
  4. AN TOÀN TUYỆT ĐỐI:
     - Đọc DATABASE_URL từ .env (không hardcode mật khẩu).
     - Mặc định chạy chế độ DRY-RUN (chỉ đọc, không sửa DB). Chỉ thực thi khi truyền `--execute`.
     - Toàn bộ thao tác chạy trong 1 PostgreSQL Transaction duy nhất (`engine.begin()`):
       gặp bất kỳ lỗi nào sẽ tự động ROLLBACK 100%.
     - Tự động chụp bản sao lưu JSON trước khi xóa (trừ khi truyền `--no-backup`).

Cách sử dụng:
  1. Xem trước tác động (Dry-run, an toàn 100% - không sửa DB):
       python -u app/shared/database/reset_db_for_defense.py

  2. Thực thi Reset sạch DB (có hỏi xác nhận + tự động backup JSON):
       python -u app/shared/database/reset_db_for_defense.py --execute

  3. Thực thi không hỏi lại (phù hợp chạy tự động):
       python -u app/shared/database/reset_db_for_defense.py --execute --yes
       python -u app/shared/database/reset_db_for_defense.py --execute --yes --no-backup
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

# Bảo vệ chống treo socket/DNS trên Windows (tuân thủ GEMINI.md)
_WATCHDOG_SEC = float(os.getenv("RESET_DB_TIMEOUT_SEC", "60"))
_wd = threading.Timer(
    _WATCHDOG_SEC,
    lambda: (print("\n[WATCHDOG_TIMEOUT] Quá thời gian chờ kết nối DB, tự thoát an toàn.", flush=True), os._exit(2)),
)
_wd.daemon = True
_wd.start()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo import được `app.*` dù chạy từ bất kỳ thư mục nào
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from sqlalchemy import text
from app.core.timezone import get_vn_datetime, get_vn_naive_datetime
from app.database.db import engine


# ── Phân loại 25 bảng chính thức của hệ thống ──────────────────────────────────

# Nhóm A: Master Data (giữ nguyên cấu trúc & dữ liệu nền)
MASTER_TABLES = [
    "hubs",
    "accounts",
    "battery_model_catalog",
    "part_categories",
    "hub_supplies",
    "maintenance_schedules",
]

# Nhóm B: Đội bay & Pin (KHÔNG BAO GIỜ XÓA — giữ nguyên toàn bộ thiết bị tại tất cả các Hub,
# bao gồm Da Kao Hub (20) & Hiep Binh Hub (21), chỉ reset trạng thái về 100% sẵn sàng)
FLEET_RESET_TABLES = [
    "drones",
    "batteries",
]

# Nhóm C: Dữ liệu nghiệp vụ & nhật ký (XÓA SẠCH theo đúng thứ tự Con -> Cha để thỏa FK)
CLEAR_ORDER = [
    "delivery_ratings",       # FK -> orders, accounts
    "telemetry_logs",         # FK -> missions, drones
    "ai_predictions",         # ref -> missions
    "mission_legs",           # FK -> missions, batteries, hubs
    "replacement_requests",   # FK -> work_orders, drones, batteries, part_categories, hubs
    "parts_requests",         # FK -> work_orders, part_categories, hubs
    "supply_transactions",    # FK -> work_orders, part_categories, hubs
    "maintenance_alerts",     # FK -> work_orders, drones, batteries (đã gỡ vòng lặp FK trước)
    "incidents",              # FK -> missions, drones
    "work_orders",            # FK -> drones, batteries, maintenance_alerts (đã gỡ vòng lặp FK trước)
    "missions",               # FK -> orders, accounts, drones, batteries
    "orders",                 # FK -> accounts, hubs
    "drone_daily_stats",      # FK -> drones
    "saved_addresses",        # FK -> accounts, hubs
    "notifications",          # ref -> accounts, drones, work_orders, missions
    "activity_logs",          # ref -> accounts
    "system_logs",            # system cron logs
]

ALL_25_TABLES = MASTER_TABLES + FLEET_RESET_TABLES + CLEAR_ORDER

# Các bảng có khóa chính BIGSERIAL/SERIAL cần reset sequence về 1 sau khi DELETE
SERIAL_ID_TABLES = [
    "delivery_ratings",
    "telemetry_logs",
    "ai_predictions",
    "mission_legs",
    "replacement_requests",
    "parts_requests",
    "supply_transactions",
    "maintenance_alerts",
    "incidents",
    "work_orders",
    "drone_daily_stats",
    "saved_addresses",
    "notifications",
    "activity_logs",
    "system_logs",
]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def _fetch_counts(conn, tables: list[str]) -> dict[str, int]:
    """Đếm số dòng hiện có của tất cả bảng tồn tại trong schema public."""
    existing = {
        r[0]
        for r in conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchall()
    }
    counts: dict[str, int] = {}
    present = [t for t in tables if t in existing]
    if not present:
        return counts
    union_sql = " UNION ALL ".join(
        f"SELECT '{t}' AS tbl, COUNT(*)::bigint AS cnt FROM \"{t}\"" for t in present
    )
    for tbl, cnt in conn.execute(text(union_sql)).fetchall():
        counts[tbl] = int(cnt)
    return counts


def _backup_before_reset(conn, tables_to_backup: list[str], output_path: Path) -> None:
    """Chụp nhanh snapshot JSON của các bảng bị sửa/xóa trước khi reset để có thể khôi phục."""
    existing = {
        r[0]
        for r in conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchall()
    }
    payload: dict[str, Any] = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": "Auto backup generated by reset_db_for_defense.py before clearing database",
        "tables": {},
    }
    for tbl in tables_to_backup:
        if tbl not in existing:
            continue
        res = conn.execute(text(f'SELECT * FROM "{tbl}"'))
        cols = list(res.keys())
        rows = [list(r) for r in res.fetchall()]
        payload["tables"][tbl] = {"columns": cols, "row_count": len(rows), "rows": rows}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=_json_default)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _seed_demo_incident_mission(
    conn,
    origin_hub_id: int = 16,
    dest_hub_id: int = 17,
) -> dict[str, Any]:
    """
    Tạo 1 mission đang bị sự cố hư linh kiện & rớt gần Hub đích (DISCONNECT_CRASH ở 85% chặng bay)
    khớp 100% logic `_trigger_disconnect_crash` trong `app/shared/drone_simulator.py`:
      - Hiện ngay trên Live Tracking (status='Failed', handling_status='Cannot continue', end_time=NOW)
      - Đầy đủ Order, Mission, MissionLeg (IN_PROGRESS), AI Prediction, 21 TelemetryLogs,
        Incident (High/Open), MaintenanceAlert (Pending), Notifications (Admin/Operator/Tech Hub đích/Customer),
        ActivityLogs, và khóa đúng 1 Drone (Maintenance) + 1 Battery gắn kèm chờ Tech Hub đích thu hồi.
    """
    now_naive = get_vn_naive_datetime()
    now_tz = get_vn_datetime()
    date_tag = now_naive.strftime("%Y%m%d")

    origin = conn.execute(
        text("SELECT id, name, latitude, longitude FROM hubs WHERE id = :hid"),
        {"hid": origin_hub_id},
    ).mappings().first()
    dest = conn.execute(
        text("SELECT id, name, latitude, longitude FROM hubs WHERE id = :hid"),
        {"hid": dest_hub_id},
    ).mappings().first()
    if not origin or not dest:
        raise RuntimeError(f"Không tìm thấy cặp Hub ({origin_hub_id} -> {dest_hub_id}) để seed incident!")

    # Lấy tài khoản thật đang hoạt động
    customer = conn.execute(
        text("""
            SELECT id, full_name, email FROM accounts
            WHERE role_id = 'CUSTOMER' AND is_active = true AND email != 'test@test.com'
            ORDER BY created_at ASC LIMIT 1
        """)
    ).mappings().first()
    operator = conn.execute(
        text("SELECT id, email FROM accounts WHERE role_id = 'OPERATOR' AND is_active = true ORDER BY created_at ASC LIMIT 1")
    ).mappings().first()
    admin_rows = conn.execute(
        text("SELECT id FROM accounts WHERE role_id IN ('ADMIN', 'OPERATOR') AND is_active = true")
    ).fetchall()
    origin_tech = conn.execute(
        text("SELECT id FROM accounts WHERE role_id = 'TECHNICIAN' AND is_active = true AND hub_id = :hid LIMIT 1"),
        {"hid": origin_hub_id},
    ).scalar()
    dest_techs = [
        r[0]
        for r in conn.execute(
            text("SELECT id, email FROM accounts WHERE role_id = 'TECHNICIAN' AND is_active = true AND hub_id = :hid"),
            {"hid": dest_hub_id},
        ).fetchall()
    ]
    dest_tech_email = conn.execute(
        text("SELECT email FROM accounts WHERE role_id = 'TECHNICIAN' AND is_active = true AND hub_id = :hid LIMIT 1"),
        {"hid": dest_hub_id},
    ).scalar()

    # Chọn 1 drone & 1 pin tương thích tại Origin Hub (ưu tiên M100 + TB48)
    drone = conn.execute(
        text("""
            SELECT id, name, model, max_speed FROM drones
            WHERE current_hub_id = :hid AND operational_status = 'Available'
            ORDER BY CASE WHEN model = 'DJI Matrice 100' THEN 0 ELSE 1 END, id ASC
            LIMIT 1
        """),
        {"hid": origin_hub_id},
    ).mappings().first()
    if not drone:
        raise RuntimeError(f"Hub {origin_hub_id} không có drone Available để seed incident!")

    bat_model_pref = (
        "DJI TB48D"
        if drone["model"] == "DJI Matrice 100"
        else ("Aurelia UltraPower 12S" if drone["model"] == "Aurelia X6 Pro" else "DJI DB2000")
    )
    battery = conn.execute(
        text("""
            SELECT id, battery_model, capacity_wh FROM batteries
            WHERE current_hub_id = :hid AND status = 'Active' AND drone_id IS NULL
            ORDER BY CASE WHEN battery_model = :bm THEN 0 ELSE 1 END, id ASC
            LIMIT 1
        """),
        {"hid": origin_hub_id, "bm": bat_model_pref},
    ).mappings().first()
    if not battery:
        raise RuntimeError(f"Hub {origin_hub_id} không có pin Active để seed incident!")

    # Sinh mã Order / Mission kế tiếp chưa tồn tại trong ngày (tránh trùng PK ở chế độ polish)
    existing_ords = [
        r[0]
        for r in conn.execute(
            text("SELECT id FROM orders WHERE id LIKE :pfx"),
            {"pfx": f"ORD-{date_tag}-%"},
        ).fetchall()
    ]
    next_ord_num = 1
    for oid in existing_ords:
        try:
            next_ord_num = max(next_ord_num, int(str(oid).rsplit("-", 1)[-1]) + 1)
        except ValueError:
            pass
    order_id = f"ORD-{date_tag}-{next_ord_num:04d}"

    existing_msns = [
        r[0]
        for r in conn.execute(
            text("SELECT id FROM missions WHERE id LIKE :pfx"),
            {"pfx": f"MSN-{date_tag}-%"},
        ).fetchall()
    ]
    next_msn_num = 1
    for mid in existing_msns:
        try:
            next_msn_num = max(next_msn_num, int(str(mid).rsplit("-", 1)[-1]) + 1)
        except ValueError:
            pass
    mission_id = f"MSN-{date_tag}-{next_msn_num:04d}"

    dist_m = round(
        _haversine_m(float(origin["latitude"]), float(origin["longitude"]), float(dest["latitude"]), float(dest["longitude"])),
        1,
    )
    est_duration_min = round((dist_m / max(float(drone["max_speed"] or 15.0), 10.0)) / 60.0, 2)
    est_energy_wh = round(max(18.0, (dist_m / 1000.0) * 6.5), 2)
    delivery_fee = round(max(25000.0, (dist_m / 1000.0) * 4500.0), -3)

    # Mốc thời gian gần hiện tại để Live Tracking giữ hiển thị trong 10 phút gần nhất
    created_at = now_naive - dt.timedelta(minutes=18)
    origin_recv_at = now_naive - dt.timedelta(minutes=12)
    departed_at = now_naive - dt.timedelta(minutes=7)
    crash_at = now_naive  # Ngay thời điểm hiện tại -> luôn thỏa end_time >= now - 10m

    # Tọa độ rơi tại 85% chặng bay (sát Hub đích -> recovery_hub = dest)
    crash_ratio = 0.85
    inc_lat = round(float(origin["latitude"]) + (float(dest["latitude"]) - float(origin["latitude"])) * crash_ratio, 6)
    inc_lng = round(float(origin["longitude"]) + (float(dest["longitude"]) - float(origin["longitude"])) * crash_ratio, 6)

    # 1. Insert Order (IN_DELIVERY, PAID)
    conn.execute(
        text("""
            INSERT INTO orders (
                id, customer_id, sender_phone, sender_email, receiver_name, receiver_phone,
                package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                delivery_mode, requested_delivery_at, estimated_window_start, estimated_window_end,
                planning_at, status, origin_received_at, origin_received_by,
                created_at, updated_at, delivery_fee, payment_status, paid_at, is_aging_priority
            ) VALUES (
                :id, :cust_id, '0909123456', :cust_email, 'Nguyen Van Minh', '0918654321',
                NULL, 'Fragile', 0.8, :orig_id, :dest_id,
                'EXPRESS', :req_at, :win_s, :win_e,
                :plan_at, 'IN_DELIVERY', :orig_recv_at, :orig_tech,
                :created_at, :now_naive, :fee, 'PAID', :created_at, false
            )
        """),
        {
            "id": order_id,
            "cust_id": customer["id"] if customer else None,
            "cust_email": customer["email"] if customer else "customer@droneoptai.com",
            "orig_id": origin_hub_id,
            "dest_id": dest_hub_id,
            "req_at": now_naive + dt.timedelta(minutes=25),
            "win_s": now_naive + dt.timedelta(minutes=5),
            "win_e": now_naive + dt.timedelta(minutes=30),
            "plan_at": origin_recv_at + dt.timedelta(minutes=2),
            "orig_recv_at": origin_recv_at,
            "orig_tech": origin_tech,
            "created_at": created_at,
            "now_naive": now_naive,
            "fee": delivery_fee,
        },
    )

    # 2. Insert Mission (Failed + Cannot continue, chờ Tech Hub đích confirm-arrival thu hồi)
    checkpoints = [
        {
            "hub_id": int(origin["id"]),
            "hub_name": origin["name"],
            "lat": float(origin["latitude"]),
            "lng": float(origin["longitude"]),
            "type": "origin",
        },
        {
            "hub_id": int(dest["id"]),
            "hub_name": dest["name"],
            "lat": float(dest["latitude"]),
            "lng": float(dest["longitude"]),
            "type": "destination",
        },
    ]
    conn.execute(
        text("""
            INSERT INTO missions (
                id, order_id, customer_id, operator_id, drone_id, battery_id,
                payload_weight, distance_m, delivery_fee,
                status, handling_status,
                scheduled_time, start_time, end_time,
                origin_hub_id, destination_hub_id, departed_at, arrived_at,
                arrival_confirmed_by, arrival_confirmed_at,
                leg_destination_type, checkpoints_json
            ) VALUES (
                :id, :order_id, :cust_id, :op_id, :drone_id, :bat_id,
                0.8, :dist_m, :fee,
                'Failed', 'Cannot continue',
                :sched, :start_t, :end_t,
                :orig_id, :dest_id, :departed_at, NULL,
                NULL, NULL,
                'destination', CAST(:cp_json AS jsonb)
            )
        """),
        {
            "id": mission_id,
            "order_id": order_id,
            "cust_id": customer["id"] if customer else None,
            "op_id": operator["id"] if operator else None,
            "drone_id": drone["id"],
            "bat_id": battery["id"],
            "dist_m": dist_m,
            "fee": delivery_fee,
            "sched": departed_at - dt.timedelta(minutes=2),
            "start_t": departed_at,
            "end_t": crash_at,
            "orig_id": origin_hub_id,
            "dest_id": dest_hub_id,
            "departed_at": departed_at,
            "cp_json": json.dumps(checkpoints, ensure_ascii=False),
        },
    )

    # 3. Insert MissionLeg (sequence_no=1, status='IN_PROGRESS' khớp _trigger_disconnect_crash)
    conn.execute(
        text("""
            INSERT INTO mission_legs (
                mission_id, sequence_no, from_hub_id, to_hub_id, battery_id,
                status, started_at, completed_at, created_at, updated_at
            ) VALUES (
                :mid, 1, :orig_id, :dest_id, :bat_id,
                'IN_PROGRESS', :started_at, NULL, :created_at, :now_naive
            )
        """),
        {
            "mid": mission_id,
            "orig_id": origin_hub_id,
            "dest_id": dest_hub_id,
            "bat_id": battery["id"],
            "started_at": departed_at,
            "created_at": origin_recv_at,
            "now_naive": now_naive,
        },
    )

    # 4. Insert AI Prediction
    conn.execute(
        text("""
            INSERT INTO ai_predictions (
                mission_id, estimated_energy_wh, estimated_duration_m,
                risk_level, recommendation_content, is_recommendation_applied, predicted_at
            ) VALUES (
                :mid, :est_e, :est_d,
                'Medium', :rec, true, :pred_at
            )
        """),
        {
            "mid": mission_id,
            "est_e": est_energy_wh,
            "est_d": est_duration_min,
            "rec": (
                f"Direct route {origin['name']} -> {dest['name']} ({dist_m/1000:.2f} km) selected for "
                f"{drone['model']} ({drone['id']}) with {battery['battery_model']}. Moderate coastal crosswind observed."
            ),
            "pred_at": now_tz - dt.timedelta(minutes=10),
        },
    )

    # 5. Insert 21 Telemetry Logs (20 bước bay mượt tới 85% + 1 điểm rơi cuối alt=0, speed=0, volt=0, pct=0)
    steps = 20
    total_flight_sec = max(60.0, (crash_at - departed_at).total_seconds())
    for i in range(1, steps + 1):
        frac = (i / steps) * crash_ratio
        lat_i = round(float(origin["latitude"]) + (float(dest["latitude"]) - float(origin["latitude"])) * frac, 6)
        lng_i = round(float(origin["longitude"]) + (float(dest["longitude"]) - float(origin["longitude"])) * frac, 6)
        ts_i = departed_at + dt.timedelta(seconds=total_flight_sec * (i / (steps + 1)))
        alt_i = round(min(45.0, 12.0 + i * 3.5), 1)
        spd_i = round(float(drone["max_speed"] or 15.0) * (0.92 if i < steps - 1 else 0.55), 2)
        bat_pct_i = round(100.0 - (18.0 * (i / steps)), 1)
        volt_i = round(25.2 - (2.4 * (i / steps)), 2)
        energy_i = round(est_energy_wh * frac, 2)
        conn.execute(
            text("""
                INSERT INTO telemetry_logs (
                    mission_id, timestamp, latitude, longitude, altitude, speed,
                    battery_voltage, battery_pct, energy_consumed_wh, wind_speed
                ) VALUES (
                    :mid, :ts, :lat, :lng, :alt, :spd, :volt, :bpct, :en, 4.8
                )
            """),
            {
                "mid": mission_id, "ts": ts_i, "lat": lat_i, "lng": lng_i,
                "alt": alt_i, "spd": spd_i, "volt": volt_i, "bpct": bat_pct_i, "en": energy_i,
            },
        )
    # Điểm rơi cuối cùng (alt=0, speed=0, battery_voltage=0, battery_pct=0)
    conn.execute(
        text("""
            INSERT INTO telemetry_logs (
                mission_id, timestamp, latitude, longitude, altitude, speed,
                battery_voltage, battery_pct, energy_consumed_wh, wind_speed
            ) VALUES (
                :mid, :ts, :lat, :lng, 0.0, 0.0, 0.0, 0.0, :en, 6.2
            )
        """),
        {"mid": mission_id, "ts": crash_at, "lat": inc_lat, "lng": inc_lng, "en": round(est_energy_wh * crash_ratio, 2)},
    )

    # 6. Khóa Drone -> Maintenance (đang nằm ngoài hiện trường chờ thu hồi) & giữ Battery gắn trên Drone
    conn.execute(
        text("""
            UPDATE drones
            SET operational_status = 'Maintenance',
                current_hub_id = NULL,
                battery_level_pct = 0
            WHERE id = :did
        """),
        {"did": drone["id"]},
    )
    conn.execute(
        text("""
            UPDATE batteries
            SET drone_id = :did,
                current_hub_id = NULL,
                charge_level_pct = 82
            WHERE id = :bid
        """),
        {"did": drone["id"], "bid": battery["id"]},
    )

    # 7. Insert Incident (HIGH / Open — hư linh kiện Motor/ESC & rớt gần Hub đích)
    inc_desc = (
        f"[DISCONNECT / CRASH] Hardware component failure (Motor #2 / ESC overheat & thrust loss) — "
        f"complete telemetry signal loss / drone crash at GPS ({inc_lat:.5f}, {inc_lng:.5f}) "
        f"near {dest['name']}. Field recovery and component replacement assigned to {dest['name']}."
    )
    inc_id = conn.execute(
        text("""
            INSERT INTO incidents (
                mission_id, drone_id, reporter_id, severity, description,
                status, reported_at, requires_technical_inspection
            ) VALUES (
                :mid, :did, :rep_id, 'High', :desc,
                'Open', :rep_at, true
            )
            RETURNING id
        """),
        {
            "mid": mission_id,
            "did": drone["id"],
            "rep_id": operator["id"] if operator else None,
            "desc": inc_desc,
            "rep_at": now_tz,
        },
    ).scalar()

    # 8. Insert MaintenanceAlert (Pending, chờ Tech Hub đích xác nhận thu hồi & tạo Work Order thay linh kiện)
    alert_desc = (
        f"Mission: {mission_id}\n"
        f"Order: {order_id}\n"
        f"Drone: {drone['id']} ({drone['name']})\n"
        f"Incident Type: DISCONNECT_CRASH (Hardware Component Failure — Motor/ESC)\n"
        f"Location: GPS ({inc_lat:.5f}, {inc_lng:.5f})\n"
        f"Recovery Hub: {dest['name']}\n"
        f"Details: Motor #2 / ESC malfunction caused forced crash near {dest['name']}. "
        f"Field recovery & part replacement required."
    )
    conn.execute(
        text("""
            INSERT INTO maintenance_alerts (
                drone_id, source, incident_id, mission_id, title, description, status, created_at
            ) VALUES (
                :did, 'Incident', :inc_id, :mid,
                :title, :desc, 'Pending', :created_at
            )
        """),
        {
            "did": drone["id"],
            "inc_id": inc_id,
            "mid": mission_id,
            "title": f"Component failure & crash near {dest['name']} — Drone {drone['id']}",
            "desc": alert_desc,
            "created_at": crash_at,
        },
    )

    # 9. Insert Notifications cho Admin, Operator, Technician Hub đích và Customer
    for (uid,) in admin_rows:
        conn.execute(
            text("""
                INSERT INTO notifications (
                    user_id, title, message, notifications_type, reference_id,
                    is_read, created_at, drone_id, mission_id
                ) VALUES (
                    :uid, :title, :msg, 'System_Alert', :ref_id,
                    false, :now_tz, :did, :mid
                )
            """),
            {
                "uid": uid,
                "title": f"CRITICAL: Drone Disconnected — Mission #{mission_id}",
                "msg": (
                    f"Drone #{drone['id']} ({drone['name']}) suffered Motor/ESC component failure and crashed "
                    f"at GPS ({inc_lat:.5f}, {inc_lng:.5f}) near {dest['name']}. Recovery team notified."
                ),
                "ref_id": str(inc_id),
                "now_tz": now_tz,
                "did": drone["id"],
                "mid": mission_id,
            },
        )

    for tid in dest_techs:
        conn.execute(
            text("""
                INSERT INTO notifications (
                    user_id, title, message, notifications_type, reference_id,
                    is_read, created_at, drone_id, mission_id
                ) VALUES (
                    :uid, :title, :msg, 'System_Alert', :ref_id,
                    false, :now_tz, :did, :mid
                )
            """),
            {
                "uid": tid,
                "title": f"Field Recovery Needed: Drone #{drone['id']}",
                "msg": (
                    f"Mission #{mission_id} crashed near {dest['name']} at GPS ({inc_lat:.5f}, {inc_lng:.5f}) "
                    f"due to Motor/ESC failure. Please recover drone & package to {dest['name']}, click Confirm Arrival, "
                    f"and create a Work Order for component replacement."
                ),
                "ref_id": str(inc_id),
                "now_tz": now_tz,
                "did": drone["id"],
                "mid": mission_id,
            },
        )

    if customer:
        conn.execute(
            text("""
                INSERT INTO notifications (
                    user_id, title, message, notifications_type, reference_id,
                    is_read, created_at, drone_id, mission_id
                ) VALUES (
                    :uid, :title, :msg, 'Mission_Status', :ref_id,
                    false, :now_tz, :did, :mid
                )
            """),
            {
                "uid": customer["id"],
                "title": f"Delivery Incident Alert — Order {order_id}",
                "msg": (
                    f"Your flight mission #{mission_id} encountered an unexpected technical incident near {dest['name']}. "
                    f"Our ground team is recovering the package and will update you shortly."
                ),
                "ref_id": order_id,
                "now_tz": now_tz,
                "did": drone["id"],
                "mid": mission_id,
            },
        )

    # 10. Insert Activity Logs
    conn.execute(
        text("""
            INSERT INTO activity_logs (
                user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
            ) VALUES
            (:op_id, :op_email, 'OPERATOR', 'CREATE_MISSION', 'mission', :mid,
             :desc1, 'success', :t1),
            (NULL, 'system@droneoptai.com', 'SYSTEM', 'INCIDENT_TRIGGERED', 'incident', :inc_id_str,
             :desc2, 'warning', :t2)
        """),
        {
            "op_id": operator["id"] if operator else None,
            "op_email": operator["email"] if operator else "operator@droneoptai.com",
            "mid": mission_id,
            "desc1": f"Assigned {drone['id']} & {battery['id']} for {order_id} ({origin['name']} -> {dest['name']})",
            "t1": now_tz - dt.timedelta(minutes=9),
            "inc_id_str": str(inc_id),
            "desc2": inc_desc,
            "t2": now_tz,
        },
    )

    return {
        "order_id": order_id,
        "mission_id": mission_id,
        "incident_id": inc_id,
        "origin_hub": f"{origin['id']} ({origin['name']})",
        "dest_hub": f"{dest['id']} ({dest['name']})",
        "dest_tech_email": dest_tech_email,
        "drone_id": drone["id"],
        "battery_id": battery["id"],
        "crash_gps": f"({inc_lat}, {inc_lng})",
    }


def _print_summary_table(before: dict[str, int], after: dict[str, int] | None, clear_tables: list[str]) -> None:
    print("\n" + "=" * 86, flush=True)
    header = f"{'Bảng':<26} | {'Nhóm':<16} | {'Trước':>8} | {'Sau' if after else 'Dự kiến sau':>12}"
    print(header, flush=True)
    print("-" * 86, flush=True)

    for tbl in ALL_25_TABLES:
        if tbl not in before:
            continue
        if tbl in MASTER_TABLES:
            grp = "GIỮ (Master)"
            expected = str(before[tbl])
        elif tbl in FLEET_RESET_TABLES:
            grp = "RESET trạng thái"
            expected = str(before[tbl])
        elif tbl in clear_tables:
            grp = "CLEAR sạch"
            expected = "0"
        else:
            grp = "GIỮ (Tùy chọn)"
            expected = str(before[tbl])

        after_val = str(after.get(tbl, "-")) if after is not None else expected
        print(f"{tbl:<26} | {grp:<16} | {before[tbl]:>8} | {after_val:>12}", flush=True)
    print("=" * 86, flush=True)


def _run_polish_cleanup(conn, now_vn: dt.datetime) -> dict[str, int]:
    """
    Chế độ Polish & Trim (Zero-Empty Clean):
    - Giữ nguyên 100% 4 In Flight + 6 Maintenance drones, các đơn thật, work orders,
      supply_transactions, activity_logs, delivery_ratings, drone_daily_stats.
    - Dọn gọn spam thông báo, giảm bớt đơn/mission hủy dư thừa, đóng bớt incidents/alerts cũ
      (vẫn đảm bảo MỌI trạng thái trên FE của 4 Role đều > 0).
    - Nạp đầy pin yếu đang rảnh ở kho và bổ sung hub_supplies dưới ngưỡng tối thiểu.
    """
    stats: dict[str, int] = {}

    # 1. Sạc đầy pin rảnh tại kho bị < 80% (không đụng pin đang gắn trên drone)
    r_bat = conn.execute(
        text("""
            UPDATE batteries
            SET charge_level_pct = 100, last_charged_at = :now_vn
            WHERE drone_id IS NULL AND status = 'Active' AND charge_level_pct < 80
        """),
        {"now_vn": now_vn},
    )
    stats["recharged_idle_batteries"] = r_bat.rowcount

    # 2. Bổ sung hub_supplies dưới ngưỡng tối thiểu lên mức khỏe
    r_sup = conn.execute(
        text("""
            UPDATE hub_supplies
            SET quantity = GREATEST(quantity, min_stock_threshold + 10, 20), updated_at = NOW()
            WHERE quantity < min_stock_threshold
        """)
    )
    stats["refilled_low_supplies"] = r_sup.rowcount

    # 3. Cắt gọn missions Cancelled dư thừa (giữ lại 2 mission Cancelled mới nhất để biểu đồ > 0)
    excess_cancelled_msns = [
        r[0]
        for r in conn.execute(
            text("""
                SELECT id FROM missions
                WHERE status = 'Cancelled'
                ORDER BY scheduled_time DESC NULLS LAST, id DESC
                OFFSET 2
            """)
        ).fetchall()
    ]
    if excess_cancelled_msns:
        conn.execute(text("DELETE FROM telemetry_logs WHERE mission_id = ANY(:ids)"), {"ids": excess_cancelled_msns})
        conn.execute(text("DELETE FROM ai_predictions WHERE mission_id = ANY(:ids)"), {"ids": excess_cancelled_msns})
        conn.execute(text("DELETE FROM mission_legs WHERE mission_id = ANY(:ids)"), {"ids": excess_cancelled_msns})
        conn.execute(text("UPDATE maintenance_alerts SET mission_id = NULL WHERE mission_id = ANY(:ids)"), {"ids": excess_cancelled_msns})
        conn.execute(text("UPDATE incidents SET mission_id = NULL WHERE mission_id = ANY(:ids)"), {"ids": excess_cancelled_msns})
        conn.execute(text("DELETE FROM missions WHERE id = ANY(:ids)"), {"ids": excess_cancelled_msns})
    stats["trimmed_cancelled_missions"] = len(excess_cancelled_msns)

    # 4. Cắt gọn orders CANCELLED dư thừa (giữ lại 2 đơn CANCELLED mới nhất, chỉ xóa đơn không còn mission nào trỏ tới)
    excess_cancelled_ords = [
        r[0]
        for r in conn.execute(
            text("""
                SELECT o.id FROM orders o
                WHERE o.status = 'CANCELLED'
                  AND NOT EXISTS (SELECT 1 FROM missions m WHERE m.order_id = o.id)
                ORDER BY o.created_at DESC NULLS LAST, o.id DESC
                OFFSET 2
            """)
        ).fetchall()
    ]
    if excess_cancelled_ords:
        conn.execute(text("DELETE FROM delivery_ratings WHERE order_id = ANY(:ids)"), {"ids": excess_cancelled_ords})
        conn.execute(text("DELETE FROM orders WHERE id = ANY(:ids)"), {"ids": excess_cancelled_ords})
    stats["trimmed_cancelled_orders"] = len(excess_cancelled_ords)

    # 5. Đóng bớt incidents Open cũ (giữ lại 2 Open mới nhất -> cộng thêm 1 demo incident = 3 Open, số còn lại Closed > 0)
    r_inc = conn.execute(
        text("""
            UPDATE incidents
            SET status = 'Closed'
            WHERE status = 'Open'
              AND id NOT IN (
                  SELECT id FROM incidents WHERE status = 'Open' ORDER BY reported_at DESC NULLS LAST, id DESC LIMIT 2
              )
        """)
    )
    stats["closed_old_incidents"] = r_inc.rowcount

    # 6. Dọn bớt maintenance_alerts Pending cũ (giữ lại 2 Pending mới nhất -> cộng thêm 1 demo alert = 3 Pending)
    r_al = conn.execute(
        text("""
            UPDATE maintenance_alerts
            SET status = 'Dismissed'
            WHERE status = 'Pending'
              AND id NOT IN (
                  SELECT id FROM maintenance_alerts WHERE status = 'Pending' ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 2
              )
        """)
    )
    stats["dismissed_old_alerts"] = r_al.rowcount

    # 7. Cắt gọn replacement_requests AWAITING_SUPPLY (giữ 2) & REJECTED (giữ 1)
    r_rep1 = conn.execute(
        text("""
            DELETE FROM replacement_requests
            WHERE status = 'AWAITING_SUPPLY'
              AND id NOT IN (
                  SELECT id FROM replacement_requests WHERE status = 'AWAITING_SUPPLY' ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 2
              )
        """)
    )
    r_rep2 = conn.execute(
        text("""
            DELETE FROM replacement_requests
            WHERE status = 'REJECTED'
              AND id NOT IN (
                  SELECT id FROM replacement_requests WHERE status = 'REJECTED' ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 1
              )
        """)
    )
    stats["trimmed_replacement_requests"] = r_rep1.rowcount + r_rep2.rowcount

    # 8. Cắt gọn parts_requests Rejected (giữ 1)
    r_pr = conn.execute(
        text("""
            DELETE FROM parts_requests
            WHERE status = 'Rejected'
              AND id NOT IN (
                  SELECT id FROM parts_requests WHERE status = 'Rejected' ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 1
              )
        """)
    )
    stats["trimmed_parts_requests"] = r_pr.rowcount

    # 9. Cắt gọn spam notifications (1,391 -> giữ lại 30 mới nhất, đánh dấu đã đọc để chuông gọn đẹp)
    r_notif_del = conn.execute(
        text("""
            DELETE FROM notifications
            WHERE id NOT IN (
                SELECT id FROM notifications ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 30
            )
        """)
    )
    conn.execute(text("UPDATE notifications SET is_read = true WHERE is_read = false"))
    stats["trimmed_spam_notifications"] = r_notif_del.rowcount

    # 10. Dọn reset_token cũ trong accounts
    conn.execute(
        text("""
            UPDATE accounts
            SET reset_token = NULL, reset_token_expires_at = NULL
            WHERE reset_token IS NOT NULL OR reset_token_expires_at IS NOT NULL
        """)
    )
    return stats


def _arm_first_flight_incident_flag(conn, mode: str) -> int:
    """
    Đặt cờ 1 lần duy nhất trong `system_logs` để chuyến bay demo đầu tiên sau khi clear
    tự động kích hoạt DISCONNECT_CRASH (được `drone_simulator.py` tiêu thụ atomic 1 lần).
    """
    conn.execute(
        text("""
            UPDATE system_logs
            SET details_json = jsonb_set(COALESCE(details_json, '{}'::jsonb), '{armed}', 'false'::jsonb, true)
            WHERE event = 'ARM_FIRST_FLIGHT_INCIDENT'
              AND COALESCE((details_json->>'armed')::boolean, false) = true
        """)
    )
    details = {
        "armed": True,
        "incident_type": "DISCONNECT_CRASH",
        "severity": "High",
        "armed_by_mode": mode,
        "armed_at": get_vn_datetime().isoformat(),
    }
    log_id = conn.execute(
        text("""
            INSERT INTO system_logs (source, level, event, message, details_json, created_at)
            VALUES (
                'reset_db_for_defense',
                'INFO',
                'ARM_FIRST_FLIGHT_INCIDENT',
                'Armed first-flight DISCONNECT_CRASH incident for defense demo (auto-consumed once by drone_simulator)',
                CAST(:details AS jsonb),
                :now_tz
            )
            RETURNING id
        """),
        {"details": json.dumps(details, ensure_ascii=False), "now_tz": get_vn_datetime()},
    ).scalar()
    return int(log_id)


def _compute_demo_delivery_fee(
    dist_m: float,
    payload_kg: float,
    package_type: str = "Documents",
    delivery_mode: str = "NORMAL",
) -> tuple[float, float, float]:
    """Tính (delivery_fee, core_fee, express_surcharge) chuẩn theo công thức BE."""
    dist_km = max(1.0, dist_m / 1000.0)
    if payload_kg <= 1.0:
        base_fee, rate_per_km, rate_per_kg = 20000.0, 4000.0, 3000.0
    elif payload_kg <= 3.0:
        base_fee, rate_per_km, rate_per_kg = 30000.0, 5000.0, 4000.0
    elif payload_kg <= 5.0:
        base_fee, rate_per_km, rate_per_kg = 45000.0, 6000.0, 5000.0
    else:
        base_fee, rate_per_km, rate_per_kg = 65000.0, 8000.0, 6000.0

    core_fee = base_fee + dist_km * rate_per_km + payload_kg * rate_per_kg
    fragile_surcharge = core_fee * 0.15 if package_type.strip().lower() == "fragile" else 0.0
    express_surcharge = core_fee * 0.25 if delivery_mode.upper() == "EXPRESS" else 0.0
    total = round(core_fee + fragile_surcharge + express_surcharge, -3)
    return float(total), round(core_fee, 2), round(express_surcharge, 2)


def _run_demo_stabilize(conn, now_vn: dt.datetime) -> dict[str, Any]:
    """
    Chế độ DEMO-STABILIZE (Mục 7):
    Giữ nguyên toàn bộ dữ liệu ổn định hiện có (Master Data, 205 Drones, 211+ Pin, 23 missions hoàn thành,
    ratings, work_orders, replacement_requests...), chỉ:
      1. Xóa các đơn AT_HUB bị quá hạn (`estimated_window_end < now_vn`), sạc pin rảnh yếu, bổ sung hub_supplies.
      2. Seed 5 đơn AT_HUB (PAID) mới còn hạn dài (gồm 2 đơn gộp Ben Thanh -> Thu Duc nằm dưới đơn EXPRESS demo).
      3. Seed 4 viên pin mới chưa phân bổ (2x DJI DB2000, 1x Aurelia UltraPower 12S, 1x DJI TB48D) -> Added this week = 4.
      4. Seed 3 đơn REFUNDED có đủ nguồn gốc & minh chứng Mục 6 (1 crash 100%, 1 WEATHER_SAFETY, 1 OPERATOR_DECISION).
      5. Seed 2 mission Failed đã đóng kín (kèm Incident Closed, Alert Resolved, WorkOrder Completed, không khóa đội bay).
      6. Seed 6 đơn CANCELLED đa dạng 5 lý do chuẩn BE để đủ 6 thanh trên Cancellation Statistics.
      7. Bật cờ ARM_FIRST_FLIGHT_INCIDENT trong system_logs để chuyến bay demo đầu tiên tự động xảy ra incident.
    """
    import re

    now_tz = get_vn_datetime()
    stats: dict[str, Any] = {}

    # ── Helper cấp ID không trùng theo ngày ────────────────────────────────────
    ord_seq_cache: dict[str, int] = {}
    msn_seq_cache: dict[str, int] = {}

    def _next_order_id(dt_ref: dt.datetime) -> str:
        tag = dt_ref.strftime("%Y%m%d")
        if tag not in ord_seq_cache:
            rows = conn.execute(text("SELECT id FROM orders WHERE id LIKE :p"), {"p": f"ORD-{tag}-%"}).fetchall()
            mx = 0
            for (oid,) in rows:
                try:
                    mx = max(mx, int(str(oid).rsplit("-", 1)[-1]))
                except ValueError:
                    pass
            ord_seq_cache[tag] = mx
        ord_seq_cache[tag] += 1
        return f"ORD-{tag}-{ord_seq_cache[tag]:04d}"

    def _next_mission_id(dt_ref: dt.datetime) -> str:
        tag = dt_ref.strftime("%Y%m%d")
        if tag not in msn_seq_cache:
            rows = conn.execute(text("SELECT id FROM missions WHERE id LIKE :p"), {"p": f"MSN-{tag}-%"}).fetchall()
            mx = 0
            for (mid,) in rows:
                try:
                    mx = max(mx, int(str(mid).rsplit("-", 1)[-1]))
                except ValueError:
                    pass
            msn_seq_cache[tag] = mx
        msn_seq_cache[tag] += 1
        return f"MSN-{tag}-{msn_seq_cache[tag]:04d}"

    # ── Tải danh sách tài khoản & Hub thật đang hoạt động ──────────────────────
    hubs_map = {
        int(r["id"]): dict(r)
        for r in conn.execute(text("SELECT id, name, latitude, longitude FROM hubs")).mappings().fetchall()
    }
    customers = conn.execute(
        text("""
            SELECT id, full_name, email FROM accounts
            WHERE role_id = 'CUSTOMER' AND is_active = true AND email != 'test@test.com'
            ORDER BY created_at ASC
        """)
    ).mappings().fetchall()
    operators = conn.execute(
        text("SELECT id, full_name, email FROM accounts WHERE role_id = 'OPERATOR' AND is_active = true ORDER BY created_at ASC")
    ).mappings().fetchall()
    admins = conn.execute(
        text("SELECT id, full_name, email FROM accounts WHERE role_id = 'ADMIN' AND is_active = true ORDER BY created_at ASC")
    ).mappings().fetchall()
    techs = conn.execute(
        text("SELECT id, full_name, email, hub_id FROM accounts WHERE role_id = 'TECHNICIAN' AND is_active = true ORDER BY created_at ASC")
    ).mappings().fetchall()

    if not customers or not operators or not admins or not techs:
        raise RuntimeError("Thiếu tài khoản thật (CUSTOMER/OPERATOR/ADMIN/TECHNICIAN) trong bảng accounts!")

    admin_acc = admins[0]
    operator_acc = operators[0]

    def _pick_customer(idx: int) -> dict[str, Any]:
        return dict(customers[idx % len(customers)])

    def _pick_tech(hub_id: int) -> dict[str, Any]:
        for t in techs:
            if t["hub_id"] == hub_id:
                return dict(t)
        return dict(techs[0])

    # ── 1. Xóa các đơn AT_HUB quá hạn + sạc pin rảnh yếu + bổ sung vật tư ─────
    overdue_ords = [
        r[0]
        for r in conn.execute(
            text("""
                SELECT id FROM orders
                WHERE status = 'AT_HUB'
                  AND estimated_window_end IS NOT NULL
                  AND estimated_window_end < :now_vn
            """),
            {"now_vn": now_vn},
        ).fetchall()
    ]
    if overdue_ords:
        ov_msns = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM missions WHERE order_id = ANY(:oids)"),
                {"oids": overdue_ords},
            ).fetchall()
        ]
        if ov_msns:
            conn.execute(text("DELETE FROM telemetry_logs WHERE mission_id = ANY(:mids)"), {"mids": ov_msns})
            conn.execute(text("DELETE FROM ai_predictions WHERE mission_id = ANY(:mids)"), {"mids": ov_msns})
            conn.execute(text("DELETE FROM mission_legs WHERE mission_id = ANY(:mids)"), {"mids": ov_msns})
            conn.execute(text("UPDATE maintenance_alerts SET mission_id = NULL WHERE mission_id = ANY(:mids)"), {"mids": ov_msns})
            conn.execute(text("UPDATE incidents SET mission_id = NULL WHERE mission_id = ANY(:mids)"), {"mids": ov_msns})
            conn.execute(text("DELETE FROM missions WHERE id = ANY(:mids)"), {"mids": ov_msns})
        conn.execute(text("DELETE FROM delivery_ratings WHERE order_id = ANY(:oids)"), {"oids": overdue_ords})
        conn.execute(text("DELETE FROM orders WHERE id = ANY(:oids)"), {"oids": overdue_ords})
    stats["deleted_overdue_at_hub_orders"] = len(overdue_ords)
    stats["deleted_overdue_order_ids"] = overdue_ords

    r_bat = conn.execute(
        text("""
            UPDATE batteries
            SET charge_level_pct = 100, last_charged_at = :now_vn
            WHERE drone_id IS NULL AND status = 'Active' AND charge_level_pct < 80
        """),
        {"now_vn": now_vn},
    )
    stats["recharged_idle_batteries"] = r_bat.rowcount

    r_sup = conn.execute(
        text("""
            UPDATE hub_supplies
            SET quantity = GREATEST(quantity, min_stock_threshold + 10, 20), updated_at = NOW()
            WHERE quantity < min_stock_threshold
        """)
    )
    stats["refilled_low_supplies"] = r_sup.rowcount

    # ── 2. Seed 5 đơn AT_HUB (PAID) mới (gồm 2 đơn gộp Ben Thanh -> Thu Duc) ──
    fresh_specs = [
        # 2 đơn gộp Ben Thanh (1) -> Thu Duc (10): NORMAL, created_at -25m / -20m, deadline +18h
        # Đảm bảo đơn EXPRESS tạo mới khi demo luôn nằm trên cùng!
        # Chuẩn FE: package_label = None khi package_type là category chuẩn (chỉ có label khi chọn 'Other').
        {
            "orig": 1, "dest": 10, "mode": "NORMAL", "kg": 0.6, "pkg_type": "Documents",
            "label": None, "receiver": "Tran Minh Hoang", "phone": "0908112233",
            "created_ago_m": 25, "win_end_h": 18, "cust_idx": 0,
        },
        {
            "orig": 1, "dest": 10, "mode": "NORMAL", "kg": 0.8, "pkg_type": "Electronics",
            "label": None, "receiver": "Le Thi Thanh Mai", "phone": "0908223344",
            "created_ago_m": 20, "win_end_h": 18, "cust_idx": 1,
        },
        # 2 đơn tuyến dài / Hub trung gian -> Vung Tau (16)
        {
            "orig": 1, "dest": 16, "mode": "NORMAL", "kg": 0.7, "pkg_type": "Documents",
            "label": None, "receiver": "Pham Quoc Bao", "phone": "0912334455",
            "created_ago_m": 18, "win_end_h": 20, "cust_idx": 2,
        },
        {
            "orig": 10, "dest": 16, "mode": "NORMAL", "kg": 0.5, "pkg_type": "Medical",
            "label": None, "receiver": "Nguyen Ngoc Han", "phone": "0912445566",
            "created_ago_m": 15, "win_end_h": 20, "cust_idx": 0,
        },
        # 1 đơn SCHEDULED nội thành Saigon (2) -> Gia Dinh (9)
        {
            "orig": 2, "dest": 9, "mode": "SCHEDULED", "kg": 0.9, "pkg_type": "Fragile",
            "label": None, "receiver": "Vo Dinh Khoa", "phone": "0913556677",
            "created_ago_m": 12, "win_end_h": 12, "cust_idx": 1,
        },
    ]
    seeded_fresh_ids: list[str] = []
    for sp in fresh_specs:
        oh = hubs_map.get(sp["orig"])
        dh = hubs_map.get(sp["dest"])
        if not oh or not dh:
            continue
        cust = _pick_customer(sp["cust_idx"])
        tech = _pick_tech(sp["orig"])
        c_at = now_vn - dt.timedelta(minutes=sp["created_ago_m"])
        recv_at = c_at + dt.timedelta(minutes=5)
        win_start = now_vn + dt.timedelta(hours=1)
        win_end = now_vn + dt.timedelta(hours=sp["win_end_h"])
        req_at = (now_vn + dt.timedelta(hours=sp["win_end_h"] - 2)) if sp["mode"] == "SCHEDULED" else win_end
        dist_m = _haversine_m(float(oh["latitude"]), float(oh["longitude"]), float(dh["latitude"]), float(dh["longitude"]))
        fee, _, _ = _compute_demo_delivery_fee(dist_m, sp["kg"], sp["pkg_type"], sp["mode"])
        oid = _next_order_id(c_at)
        # Không chèn URL giả (.local) gây broken image trên FE; để mảng rỗng chờ ảnh thật (hoặc giữ nguyên ảnh hiện có trên DB)
        proof_stage1: list[dict[str, Any]] = []
        conn.execute(
            text("""
                INSERT INTO orders (
                    id, customer_id, sender_phone, sender_email, receiver_name, receiver_phone,
                    package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                    delivery_mode, requested_delivery_at, estimated_window_start, estimated_window_end,
                    status, origin_received_at, origin_received_by,
                    created_at, updated_at, delivery_fee, payment_status, paid_at,
                    is_aging_priority, proof_images_json
                ) VALUES (
                    :id, :cust_id, '0909000111', :cust_email, :recv_name, :recv_phone,
                    :pkg_label, :pkg_type, :kg, :orig, :dest,
                    :mode, :req_at, :win_s, :win_e,
                    'AT_HUB', :recv_at, :tech_id,
                    :c_at, :recv_at, :fee, 'PAID', :c_at,
                    false, CAST(:proof AS jsonb)
                )
            """),
            {
                "id": oid, "cust_id": cust["id"], "cust_email": cust["email"],
                "recv_name": sp["receiver"], "recv_phone": sp["phone"],
                "pkg_label": sp["label"], "pkg_type": sp["pkg_type"], "kg": sp["kg"],
                "orig": sp["orig"], "dest": sp["dest"], "mode": sp["mode"],
                "req_at": req_at, "win_s": win_start, "win_e": win_end,
                "recv_at": recv_at, "tech_id": tech["id"],
                "c_at": c_at, "fee": fee, "proof": json.dumps(proof_stage1, ensure_ascii=False),
            },
        )
        conn.execute(
            text("""
                INSERT INTO activity_logs (
                    user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
                ) VALUES
                (:cust_id, :cust_email, 'CUSTOMER', 'CREATE_ORDER', 'order', :oid, :d1, 'success', :t1),
                (:tech_id, :tech_email, 'TECHNICIAN', 'RECEIVE_ORDER_AT_ORIGIN_HUB', 'order', :oid, :d2, 'success', :t2)
            """),
            {
                "cust_id": cust["id"], "cust_email": cust["email"], "oid": oid,
                "d1": f"Created {sp['mode']} order {oid} ({oh['name']} -> {dh['name']}, {sp['kg']} kg)",
                "t1": now_tz - dt.timedelta(minutes=sp["created_ago_m"]),
                "tech_id": tech["id"], "tech_email": tech["email"],
                "d2": f"Received package {oid} at {oh['name']} (Stage 1 proof verified)",
                "t2": now_tz - dt.timedelta(minutes=max(1, sp["created_ago_m"] - 5)),
            },
        )
        seeded_fresh_ids.append(oid)
    stats["seeded_fresh_at_hub_orders"] = seeded_fresh_ids

    # ── 3. Seed 4 viên pin mới chưa phân bổ (Added this week = 4, hết Out of stock) ─
    max_bat_num = 0
    for (bid,) in conn.execute(text("SELECT id FROM batteries")).fetchall():
        m = re.search(r"BAT(\d+)$", str(bid))
        if m:
            max_bat_num = max(max_bat_num, int(m.group(1)))
    max_sn_num = 0
    for (sn,) in conn.execute(text("SELECT serial_number FROM batteries")).fetchall():
        m = re.search(r"SN-BAT-(\d+)$", str(sn))
        if m:
            max_sn_num = max(max_sn_num, int(m.group(1)))

    new_bat_specs = [
        ("DB20", "DJI DB2000", 980.0, 1),
        ("DB20", "DJI DB2000", 980.0, 2),
        ("UP12", "Aurelia UltraPower 12S", 444.0, 2),
        ("TB48", "DJI TB48D", 130.0, 3),
    ]
    seeded_battery_ids: list[str] = []
    for pfx, model_name, cap_wh, days_ago in new_bat_specs:
        max_bat_num += 1
        max_sn_num += 1
        bat_id = f"{pfx}-BAT{max_bat_num:04d}"
        sn = f"SN-BAT-{max_sn_num:04d}"
        c_tz = now_tz - dt.timedelta(days=days_ago, hours=2)
        c_naive = now_vn - dt.timedelta(days=days_ago, hours=2)
        conn.execute(
            text("""
                INSERT INTO batteries (
                    id, drone_id, serial_number, capacity_wh, status,
                    current_hub_id, charge_level_pct, create_at, update_at,
                    battery_model, last_charged_at, work_order_id
                ) VALUES (
                    :id, NULL, :sn, :cap, 'Active',
                    NULL, 100, :c_tz, :c_tz,
                    :model, :c_naive, NULL
                )
            """),
            {"id": bat_id, "sn": sn, "cap": cap_wh, "c_tz": c_tz, "model": model_name, "c_naive": c_naive},
        )
        conn.execute(
            text("""
                INSERT INTO activity_logs (
                    user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
                ) VALUES (
                    :uid, :uemail, 'ADMIN', 'CREATE_BATTERY', 'battery', :bid, :desc, 'success', :c_tz
                )
            """),
            {
                "uid": admin_acc["id"], "uemail": admin_acc["email"], "bid": bat_id,
                "desc": f"Added new unassigned battery {bat_id} ({model_name}, {cap_wh:.0f} Wh) to central stock",
                "c_tz": c_tz,
            },
        )
        seeded_battery_ids.append(bat_id)
    stats["seeded_unassigned_batteries"] = seeded_battery_ids

    # ── Chuẩn bị Drone & Pin tham chiếu cho dữ liệu lịch sử (KHÔNG khóa trạng thái hiện tại) ─
    hist_drones = conn.execute(
        text("""
            SELECT id, name, model, max_speed, current_hub_id FROM drones
            WHERE operational_status = 'Available'
            ORDER BY id ASC LIMIT 5
        """)
    ).mappings().fetchall()
    hist_bats = conn.execute(
        text("""
            SELECT id, battery_model, capacity_wh FROM batteries
            WHERE status = 'Active' AND current_hub_id IS NOT NULL
            ORDER BY id ASC LIMIT 5
        """)
    ).mappings().fetchall()
    if not hist_drones or not hist_bats:
        raise RuntimeError("Không tìm thấy drone/battery để gắn nguồn gốc cho lịch sử Refund & Failed Mission!")

    def _pick_hist_drone(i: int) -> dict[str, Any]:
        return dict(hist_drones[i % len(hist_drones)])

    def _pick_hist_bat(i: int) -> dict[str, Any]:
        return dict(hist_bats[i % len(hist_bats)])

    # ── 4 & 5. Seed 3 đơn REFUNDED + 2 Failed Missions đã đóng kín ─────────────
    #   - Failed #1 (3 ngày trước) ĐỒNG THỜI là Refund #1 (hoàn 100% do sự cố kỹ thuật)
    #   - Failed #2 (4 ngày trước): sự cố đã thu hồi & hoàn tất WorkOrder (Order CANCELLED, UNPAID hoặc đã giải quyết)
    #   - Refund #2 (2 ngày trước): EXPRESS giao trễ do WEATHER_SAFETY (hoàn 100% phụ phí Express, có Open-Meteo snapshot)
    #   - Refund #3 (1 ngày trước): EXPRESS giao trễ do OPERATOR_DECISION (hoàn 100% phụ phí Express + 20% core fee, SLA violation)
    seeded_refund_ids: list[str] = []
    seeded_failed_mids: list[str] = []

    failed_specs = [
        {
            "days_ago": 3, "orig": 1, "dest": 10, "mode": "EXPRESS", "kg": 0.8, "pkg_type": "Electronics",
            "label": None, "refund_100": True,
            "issue": "Motor #3 ESC over-current protection triggered at 68% flight progress",
            "diagnosis": "ESC MOSFET shorted under gust load; replaced ESC unit & Motor #3 and passed bench hover test.",
            "cust_idx": 0, "drone_idx": 0, "bat_idx": 0,
        },
        {
            "days_ago": 4, "orig": 2, "dest": 9, "mode": "NORMAL", "kg": 0.9, "pkg_type": "Documents",
            "label": None, "refund_100": False,
            "issue": "GPS/IMU compass interference caused forced emergency landing at 74% route",
            "diagnosis": "Recalibrated IMU/compass module, replaced damaged landing skid, passed full pre-flight check.",
            "cust_idx": 1, "drone_idx": 1, "bat_idx": 1,
        },
    ]
    for fsp in failed_specs:
        oh = hubs_map.get(fsp["orig"]) or next(iter(hubs_map.values()))
        dh = hubs_map.get(fsp["dest"]) or next(iter(hubs_map.values()))
        cust = _pick_customer(fsp["cust_idx"])
        orig_tech = _pick_tech(oh["id"])
        dest_tech = _pick_tech(dh["id"])
        drn = _pick_hist_drone(fsp["drone_idx"])
        bat = _pick_hist_bat(fsp["bat_idx"])

        t_base_naive = now_vn - dt.timedelta(days=fsp["days_ago"], hours=3)
        t_base_tz = now_tz - dt.timedelta(days=fsp["days_ago"], hours=3)
        t_recv = t_base_naive + dt.timedelta(minutes=10)
        t_dep = t_base_naive + dt.timedelta(minutes=20)
        t_crash = t_base_naive + dt.timedelta(minutes=32)
        t_confirm = t_base_naive + dt.timedelta(hours=1, minutes=10)
        t_wo_done = t_base_naive + dt.timedelta(hours=3)
        t_wo_done_tz = t_base_tz + dt.timedelta(hours=3)

        dist_m = round(_haversine_m(float(oh["latitude"]), float(oh["longitude"]), float(dh["latitude"]), float(dh["longitude"])), 1)
        fee, _, _ = _compute_demo_delivery_fee(dist_m, fsp["kg"], fsp["pkg_type"], fsp["mode"])
        oid = _next_order_id(t_base_naive)
        mid = _next_mission_id(t_base_naive)

        inc_lat = round(float(oh["latitude"]) + (float(dh["latitude"]) - float(oh["latitude"])) * 0.72, 6)
        inc_lng = round(float(oh["longitude"]) + (float(dh["longitude"]) - float(oh["longitude"])) * 0.72, 6)

        pay_status = "REFUNDED" if fsp["refund_100"] else "PAID"
        ref_amt = fee if fsp["refund_100"] else None
        ref_reason = (
            f"100% refund due to flight incident on mission {mid} ({fsp['issue']})"
            if fsp["refund_100"]
            else None
        )
        ref_status = "COMPLETED" if fsp["refund_100"] else None
        ref_at = (t_confirm + dt.timedelta(minutes=20)) if fsp["refund_100"] else None

        conn.execute(
            text("""
                INSERT INTO orders (
                    id, customer_id, sender_phone, sender_email, receiver_name, receiver_phone,
                    package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                    delivery_mode, requested_delivery_at, estimated_window_start, estimated_window_end,
                    planning_at, status, origin_received_at, origin_received_by,
                    created_at, updated_at, cancelled_at, delivery_fee,
                    payment_status, paid_at, refund_at, refund_amount, refund_reason, refund_status,
                    cancellation_reason, is_aging_priority
                ) VALUES (
                    :id, :cust_id, '0909123000', :cust_email, 'Nguyen Hoang Nam', '0909777888',
                    :label, :pkg_type, :kg, :orig, :dest,
                    :mode, :req_at, :win_s, :win_e,
                    :plan_at, 'CANCELLED', :recv_at, :orig_tech,
                    :c_at, :u_at, :can_at, :fee,
                    :pay_st, :c_at, :ref_at, :ref_amt, :ref_rsn, :ref_st,
                    'incident_recovery_cancelled', false
                )
            """),
            {
                "id": oid, "cust_id": cust["id"], "cust_email": cust["email"],
                "label": fsp["label"], "pkg_type": fsp["pkg_type"], "kg": fsp["kg"],
                "orig": oh["id"], "dest": dh["id"], "mode": fsp["mode"],
                "req_at": t_base_naive + dt.timedelta(hours=1),
                "win_s": t_base_naive + dt.timedelta(minutes=25),
                "win_e": t_base_naive + dt.timedelta(hours=1, minutes=15),
                "plan_at": t_recv + dt.timedelta(minutes=4),
                "recv_at": t_recv, "orig_tech": orig_tech["id"],
                "c_at": t_base_naive, "u_at": t_wo_done, "can_at": t_confirm, "fee": fee,
                "pay_st": pay_status, "ref_at": ref_at, "ref_amt": ref_amt,
                "ref_rsn": ref_reason, "ref_st": ref_status,
            },
        )
        if fsp["refund_100"]:
            seeded_refund_ids.append(oid)

        cps = [
            {"hub_id": oh["id"], "hub_name": oh["name"], "lat": float(oh["latitude"]), "lng": float(oh["longitude"]), "type": "origin"},
            {"hub_id": dh["id"], "hub_name": dh["name"], "lat": float(dh["latitude"]), "lng": float(dh["longitude"]), "type": "destination"},
        ]
        conn.execute(
            text("""
                INSERT INTO missions (
                    id, order_id, customer_id, operator_id, drone_id, battery_id,
                    payload_weight, distance_m, delivery_fee,
                    status, handling_status,
                    scheduled_time, start_time, end_time,
                    origin_hub_id, destination_hub_id, departed_at, arrived_at,
                    arrival_confirmed_by, arrival_confirmed_at, battery_charge_at_arrival,
                    leg_destination_type, checkpoints_json
                ) VALUES (
                    :mid, :oid, :cust_id, :op_id, :did, :bid,
                    :kg, :dist_m, :fee,
                    'Failed', NULL,
                    :sched, :start_t, :end_t,
                    :orig, :dest, :dep, :arr,
                    :conf_by, :conf_at, 71.0,
                    'destination', CAST(:cps AS jsonb)
                )
            """),
            {
                "mid": mid, "oid": oid, "cust_id": cust["id"], "op_id": operator_acc["id"],
                "did": drn["id"], "bid": bat["id"], "kg": fsp["kg"], "dist_m": dist_m, "fee": fee,
                "sched": t_dep - dt.timedelta(minutes=3), "start_t": t_dep, "end_t": t_crash,
                "orig": oh["id"], "dest": dh["id"], "dep": t_dep, "arr": t_crash,
                "conf_by": dest_tech["id"], "conf_at": t_confirm,
                "cps": json.dumps(cps, ensure_ascii=False),
            },
        )
        conn.execute(
            text("""
                INSERT INTO mission_legs (
                    mission_id, sequence_no, from_hub_id, to_hub_id, battery_id,
                    status, started_at, completed_at, created_at, updated_at
                ) VALUES (
                    :mid, 1, :orig, :dest, :bid,
                    'COMPLETED', :st, :comp, :st, :comp
                )
            """),
            {"mid": mid, "orig": oh["id"], "dest": dh["id"], "bid": bat["id"], "st": t_dep, "comp": t_confirm},
        )
        est_dur = round((dist_m / max(float(drn["max_speed"] or 15.0), 10.0)) / 60.0, 2)
        est_wh = round(max(18.0, (dist_m / 1000.0) * 6.5), 2)
        conn.execute(
            text("""
                INSERT INTO ai_predictions (
                    mission_id, estimated_energy_wh, estimated_duration_m,
                    risk_level, recommendation_content, is_recommendation_applied, predicted_at
                ) VALUES (
                    :mid, :ewh, :edur, 'Medium', :rec, true, :pat
                )
            """),
            {
                "mid": mid, "ewh": est_wh, "edur": est_dur,
                "rec": f"Direct route {oh['name']} -> {dh['name']} ({dist_m/1000:.2f} km) using {drn['model']} ({drn['id']}).",
                "pat": t_base_tz + dt.timedelta(minutes=15),
            },
        )
        # 3 điểm telemetry (khởi hành, giữa chặng, điểm sự cố kèm Open-Meteo weather)
        for frac, alt, spd, bpct, volt, ts_off in [
            (0.05, 35.0, 14.5, 97.0, 24.9, 2),
            (0.45, 45.0, 15.0, 84.0, 24.1, 7),
            (0.72, 0.0, 0.0, 0.0, 0.0, 12),
        ]:
            lat_p = round(float(oh["latitude"]) + (float(dh["latitude"]) - float(oh["latitude"])) * frac, 6)
            lng_p = round(float(oh["longitude"]) + (float(dh["longitude"]) - float(oh["longitude"])) * frac, 6)
            conn.execute(
                text("""
                    INSERT INTO telemetry_logs (
                        mission_id, timestamp, latitude, longitude, altitude, speed,
                        battery_voltage, battery_pct, energy_consumed_wh, wind_speed,
                        weather_temperature, weather_humidity, weather_wind_speed, weather_wind_gust, weather_precipitation
                    ) VALUES (
                        :mid, :ts, :lat, :lng, :alt, :spd,
                        :volt, :bpct, :en, 5.8,
                        30.5, 76.0, 5.8, 9.2, 0.4
                    )
                """),
                {
                    "mid": mid, "ts": t_dep + dt.timedelta(minutes=ts_off),
                    "lat": lat_p, "lng": lng_p, "alt": alt, "spd": spd,
                    "volt": volt, "bpct": bpct, "en": round(est_wh * frac, 2),
                },
            )
        inc_id = conn.execute(
            text("""
                INSERT INTO incidents (
                    mission_id, drone_id, reporter_id, severity, description,
                    status, reported_at, requires_technical_inspection
                ) VALUES (
                    :mid, :did, :rep_id, 'High', :desc,
                    'Closed', :rep_at, true
                )
                RETURNING id
            """),
            {
                "mid": mid, "did": drn["id"], "rep_id": operator_acc["id"],
                "desc": (
                    f"[DISCONNECT / CRASH] {fsp['issue']} at GPS ({inc_lat:.5f}, {inc_lng:.5f}) near {dh['name']}. "
                    f"Weather (Open-Meteo): 30.5°C, wind 5.8 m/s (gust 9.2 m/s), rain 0.4 mm. "
                    f"Field recovery and maintenance completed."
                ),
                "rep_at": t_base_tz + dt.timedelta(minutes=32),
            },
        ).scalar()
        alert_id = conn.execute(
            text("""
                INSERT INTO maintenance_alerts (
                    drone_id, source, incident_id, mission_id, title, description,
                    status, handled_by, created_at, handled_at
                ) VALUES (
                    :did, 'Incident', :inc_id, :mid, :title, :desc,
                    'Resolved', :tech_id, :c_at, :h_at
                )
                RETURNING id
            """),
            {
                "did": drn["id"], "inc_id": inc_id, "mid": mid,
                "title": f"Resolved incident recovery — {drn['id']} ({mid})",
                "desc": f"Mission: {mid}\nOrder: {oid}\nIssue: {fsp['issue']}\nResolution: {fsp['diagnosis']}",
                "tech_id": dest_tech["id"], "c_at": t_crash, "h_at": t_wo_done,
            },
        ).scalar()
        wo_id = conn.execute(
            text("""
                INSERT INTO work_orders (
                    drone_id, battery_id, technician_id, issue_description, action_taken,
                    status, resolved_at, alert_id, title, priority,
                    created_by, assigned_to, scheduled_at, started_at, completed_at,
                    diagnosis, resulting_drone_status, record_completed_at,
                    inspection_items_json, status_logs_json, created_at
                ) VALUES (
                    :did, :bid, :tech_id, :issue, :diag,
                    'Completed', :done_tz, :aid, :title, 'High',
                    :tech_id, :tech_id, :t_conf, :t_conf, :done_naive,
                    :diag, 'Available', :done_naive,
                    CAST(:insp AS jsonb), CAST(:slogs AS jsonb), :c_tz
                )
                RETURNING id
            """),
            {
                "did": drn["id"], "bid": bat["id"], "tech_id": dest_tech["id"],
                "issue": fsp["issue"], "diag": fsp["diagnosis"], "done_tz": t_wo_done_tz,
                "aid": alert_id, "title": f"Post-incident inspection & repair ({mid})",
                "t_conf": t_confirm, "done_naive": t_wo_done,
                "insp": json.dumps([
                    {"item": "Inspect motor & ESC telemetry logs", "passed": True},
                    {"item": "Verify airframe & propeller integrity", "passed": True},
                    {"item": "Bench hover test passed", "passed": True},
                ]),
                "slogs": json.dumps([
                    {"status": "Pending", "at": (t_base_tz + dt.timedelta(hours=1, minutes=10)).isoformat(), "by": dest_tech["id"]},
                    {"status": "Completed", "at": t_wo_done_tz.isoformat(), "by": dest_tech["id"]},
                ]),
                "c_tz": t_base_tz + dt.timedelta(hours=1, minutes=10),
            },
        ).scalar()
        conn.execute(
            text("UPDATE maintenance_alerts SET work_order_id = :wid WHERE id = :aid"),
            {"wid": wo_id, "aid": alert_id},
        )
        conn.execute(
            text("""
                INSERT INTO activity_logs (
                    user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
                ) VALUES
                (:tech_id, :tech_email, 'TECHNICIAN', 'COMPLETE_WORK_ORDER', 'work_order', :wid_s, :d1, 'success', :t_done)
            """),
            {
                "tech_id": dest_tech["id"], "tech_email": dest_tech["email"],
                "wid_s": str(wo_id), "d1": f"Completed repair WO #{wo_id} for {drn['id']} after incident on {mid}",
                "t_done": t_wo_done_tz,
            },
        )
        if fsp["refund_100"]:
            conn.execute(
                text("""
                    INSERT INTO activity_logs (
                        user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
                    ) VALUES (
                        :adm_id, :adm_email, 'ADMIN', 'REFUND_ORDER', 'order', :oid, :desc, 'success', :t_ref
                    )
                """),
                {
                    "adm_id": admin_acc["id"], "adm_email": admin_acc["email"], "oid": oid,
                    "desc": f"Processed 100% refund ({int(fee):,} VND) for {oid} after failed mission {mid}",
                    "t_ref": t_base_tz + dt.timedelta(hours=1, minutes=30),
                },
            )
            conn.execute(
                text("""
                    INSERT INTO notifications (
                        user_id, title, message, notifications_type, reference_id, is_read, created_at, mission_id
                    ) VALUES (
                        :uid, :title, :msg, 'Payment', :oid, true, :t_ref, :mid
                    )
                """),
                {
                    "uid": cust["id"], "title": f"Refund completed — Order {oid}",
                    "msg": f"Full refund of {int(fee):,} VND has been processed for Order {oid} due to technical incident on {mid}.",
                    "oid": oid, "t_ref": t_base_tz + dt.timedelta(hours=1, minutes=30), "mid": mid,
                },
            )
        seeded_failed_mids.append(mid)

    stats["seeded_closed_failed_missions"] = seeded_failed_mids

    # ── Refund #2 (WEATHER_SAFETY) & Refund #3 (OPERATOR_DECISION) — khớp Mục 6 ─
    express_refund_specs = [
        {
            "days_ago": 2, "orig": 1, "dest": 10, "kg": 0.7, "pkg_type": "Documents",
            "label": None,
            "reason_type": "WEATHER_SAFETY", "strategy": "SAFE", "late_min": 18.5,
            "note": "Heavy gust & rain cells detected along direct FAST corridor; rerouted via SAFE corridor.",
            "weather": {
                "source": "Open-Meteo",
                "latitude": 10.8124, "longitude": 106.7312,
                "temperature_c": 27.4, "wind_speed": 12.8, "wind_speed_ms": 12.8,
                "wind_gust": 18.4, "wind_gust_ms": 18.4,
                "precipitation": 7.6, "precipitation_mm": 7.6, "humidity_pct": 91.0,
                "condition_label": "Heavy Rain & Gusts",
                "is_bad_weather": True,
                "warning_message": "High wind gusts (18.4 m/s) and heavy rain (7.6 mm/h)",
                "weather_summary": "27.4°C, Wind 12.8 m/s (Gust 18.4 m/s), Rain 7.6 mm/h",
            },
            "sla_violation": False, "cust_idx": 1, "drone_idx": 2, "bat_idx": 2,
        },
        {
            "days_ago": 1, "orig": 2, "dest": 10, "kg": 0.9, "pkg_type": "Electronics",
            "label": None,
            "reason_type": "OPERATOR_DECISION", "strategy": "ECO", "late_min": 24.0,
            "note": "Operator manually selected ECO route instead of FAST for battery conservation.",
            "weather": None,
            "sla_violation": True, "cust_idx": 2, "drone_idx": 3, "bat_idx": 3,
        },
    ]
    for rsp in express_refund_specs:
        oh = hubs_map.get(rsp["orig"]) or next(iter(hubs_map.values()))
        dh = hubs_map.get(rsp["dest"]) or next(iter(hubs_map.values()))
        cust = _pick_customer(rsp["cust_idx"])
        orig_tech = _pick_tech(oh["id"])
        dest_tech = _pick_tech(dh["id"])
        drn = _pick_hist_drone(rsp["drone_idx"])
        bat = _pick_hist_bat(rsp["bat_idx"])

        t_base_naive = now_vn - dt.timedelta(days=rsp["days_ago"], hours=4)
        t_base_tz = now_tz - dt.timedelta(days=rsp["days_ago"], hours=4)
        t_recv = t_base_naive + dt.timedelta(minutes=8)
        t_dep = t_base_naive + dt.timedelta(minutes=16)
        win_end = t_base_naive + dt.timedelta(minutes=45)
        t_arr = win_end + dt.timedelta(minutes=rsp["late_min"])
        t_arr_tz = t_base_tz + dt.timedelta(minutes=45 + rsp["late_min"])

        dist_m = round(_haversine_m(float(oh["latitude"]), float(oh["longitude"]), float(dh["latitude"]), float(dh["longitude"])), 1)
        fee, core_fee, exp_surcharge = _compute_demo_delivery_fee(dist_m, rsp["kg"], rsp["pkg_type"], "EXPRESS")
        if rsp["reason_type"] == "WEATHER_SAFETY":
            refund_amt = round(exp_surcharge, 2)
            policy_code = "WEATHER_EXPRESS_SURCHARGE_REFUND"
            ref_reason = (
                f"Auto late-delivery compensation ({rsp['late_min']} min late): "
                f"100% Express surcharge refunded ({int(refund_amt):,} VND) due to WEATHER_SAFETY route override."
            )
        else:
            refund_amt = round(exp_surcharge + core_fee * 0.20, 2)
            policy_code = "OPERATOR_OVERRIDE_EXPRESS_PLUS_20PCT_CORE_REFUND"
            ref_reason = (
                f"Auto late-delivery compensation ({rsp['late_min']} min late): "
                f"100% Express surcharge + 20% core fee partial refund ({int(refund_amt):,} VND) due to OPERATOR_DECISION."
            )

        oid = _next_order_id(t_base_naive)
        mid = _next_mission_id(t_base_naive)

        weather_ev = dict(rsp["weather"], timestamp=t_base_tz.isoformat()) if rsp["weather"] else None
        route_override_entry = {
            "type": "route_override",
            "delivery_mode": "EXPRESS",
            "affected_express_order_ids": [oid],
            "selected_route_id": f"RT-{oh['id']}-{dh['id']}-{rsp['strategy']}",
            "selected_strategy_code": rsp["strategy"],
            "reason_type": rsp["reason_type"],
            "note": rsp["note"],
            "operator_id": operator_acc["id"],
            "recorded_at": (t_base_tz + dt.timedelta(minutes=14)).isoformat(),
            "weather_evidence": weather_ev,
        }
        late_comp_entry = {
            "type": "late_delivery_compensation",
            "order_id": oid,
            "mission_id": mid,
            "delivery_mode": "EXPRESS",
            "reason_type": rsp["reason_type"],
            "policy_code": policy_code,
            "operator_sla_violation": rsp["sla_violation"],
            "late_minutes": rsp["late_min"],
            "core_fee": core_fee,
            "express_surcharge": exp_surcharge,
            "refund_amount": refund_amt,
            "refund_status": "COMPLETED",
            "evaluated_at": t_arr_tz.isoformat(),
            "weather_evidence": weather_ev,
        }
        cps = [
            {"hub_id": oh["id"], "hub_name": oh["name"], "lat": float(oh["latitude"]), "lng": float(oh["longitude"]), "type": "origin"},
            {"hub_id": dh["id"], "hub_name": dh["name"], "lat": float(dh["latitude"]), "lng": float(dh["longitude"]), "type": "destination"},
            route_override_entry,
            late_comp_entry,
        ]

        conn.execute(
            text("""
                INSERT INTO orders (
                    id, customer_id, sender_phone, sender_email, receiver_name, receiver_phone,
                    package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                    delivery_mode, requested_delivery_at, estimated_window_start, estimated_window_end,
                    planning_at, status, origin_received_at, origin_received_by,
                    destination_received_at, destination_received_by,
                    created_at, updated_at, delivery_fee, payment_status, paid_at,
                    refund_at, refund_amount, refund_reason, refund_status, is_aging_priority
                ) VALUES (
                    :id, :cust_id, '0909888111', :cust_email, 'Pham Thi Lan Anh', '0909666222',
                    :label, :pkg_type, :kg, :orig, :dest,
                    'EXPRESS', :win_e, :win_s, :win_e,
                    :plan_at, 'DELIVERED_TO_HUB', :recv_at, :orig_tech,
                    :arr_at, :dest_tech,
                    :c_at, :arr_at, :fee, 'REFUNDED', :c_at,
                    :arr_at, :ref_amt, :ref_rsn, 'COMPLETED', false
                )
            """),
            {
                "id": oid, "cust_id": cust["id"], "cust_email": cust["email"],
                "label": rsp["label"], "pkg_type": rsp["pkg_type"], "kg": rsp["kg"],
                "orig": oh["id"], "dest": dh["id"],
                "win_s": t_base_naive + dt.timedelta(minutes=20), "win_e": win_end,
                "plan_at": t_recv + dt.timedelta(minutes=4), "recv_at": t_recv, "orig_tech": orig_tech["id"],
                "arr_at": t_arr, "dest_tech": dest_tech["id"],
                "c_at": t_base_naive, "fee": fee, "ref_amt": refund_amt, "ref_rsn": ref_reason,
            },
        )
        conn.execute(
            text("""
                INSERT INTO missions (
                    id, order_id, customer_id, operator_id, drone_id, battery_id,
                    payload_weight, distance_m, delivery_fee,
                    status, handling_status,
                    scheduled_time, start_time, end_time,
                    origin_hub_id, destination_hub_id, departed_at, arrived_at,
                    arrival_confirmed_by, arrival_confirmed_at, battery_charge_at_arrival,
                    leg_destination_type, checkpoints_json
                ) VALUES (
                    :mid, :oid, :cust_id, :op_id, :did, :bid,
                    :kg, :dist_m, :fee,
                    'Mission completed', NULL,
                    :sched, :dep, :arr,
                    :orig, :dest, :dep, :arr,
                    :dest_tech, :arr, 68.0,
                    'destination', CAST(:cps AS jsonb)
                )
            """),
            {
                "mid": mid, "oid": oid, "cust_id": cust["id"], "op_id": operator_acc["id"],
                "did": drn["id"], "bid": bat["id"], "kg": rsp["kg"], "dist_m": dist_m, "fee": fee,
                "sched": t_dep - dt.timedelta(minutes=2), "dep": t_dep, "arr": t_arr,
                "orig": oh["id"], "dest": dh["id"], "dest_tech": dest_tech["id"],
                "cps": json.dumps(cps, ensure_ascii=False),
            },
        )
        conn.execute(
            text("""
                INSERT INTO mission_legs (
                    mission_id, sequence_no, from_hub_id, to_hub_id, battery_id,
                    status, started_at, completed_at, created_at, updated_at
                ) VALUES (
                    :mid, 1, :orig, :dest, :bid,
                    'COMPLETED', :dep, :arr, :dep, :arr
                )
            """),
            {"mid": mid, "orig": oh["id"], "dest": dh["id"], "bid": bat["id"], "dep": t_dep, "arr": t_arr},
        )
        est_dur = round((dist_m / max(float(drn["max_speed"] or 15.0), 10.0)) / 60.0, 2)
        est_wh = round(max(18.0, (dist_m / 1000.0) * 6.5), 2)
        conn.execute(
            text("""
                INSERT INTO ai_predictions (
                    mission_id, estimated_energy_wh, estimated_duration_m,
                    risk_level, recommendation_content, is_recommendation_applied, predicted_at
                ) VALUES (
                    :mid, :ewh, :edur, :risk, :rec, false, :pat
                )
            """),
            {
                "mid": mid, "ewh": est_wh, "edur": est_dur,
                "risk": "High" if rsp["reason_type"] == "WEATHER_SAFETY" else "Low",
                "rec": f"FAST route recommended for EXPRESS {oid}; overridden to {rsp['strategy']} ({rsp['reason_type']}).",
                "pat": t_base_tz + dt.timedelta(minutes=12),
            },
        )
        conn.execute(
            text("""
                INSERT INTO activity_logs (
                    user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
                ) VALUES (
                    :op_id, :op_email, 'OPERATOR', :act, 'order', :oid, :desc, :st, :tat
                )
            """),
            {
                "op_id": operator_acc["id"], "op_email": operator_acc["email"],
                "act": "OPERATOR_SLA_VIOLATION" if rsp["sla_violation"] else "LATE_DELIVERY_WEATHER_REFUND",
                "oid": oid, "desc": ref_reason,
                "st": "warning" if rsp["sla_violation"] else "success",
                "tat": t_arr_tz,
            },
        )
        conn.execute(
            text("""
                INSERT INTO notifications (
                    user_id, title, message, notifications_type, reference_id, is_read, created_at, mission_id
                ) VALUES (
                    :uid, :title, :msg, 'Payment', :oid, true, :tat, :mid
                )
            """),
            {
                "uid": cust["id"], "title": f"Late delivery refund — Order {oid}",
                "msg": ref_reason, "oid": oid, "tat": t_arr_tz, "mid": mid,
            },
        )
        seeded_refund_ids.append(oid)

    stats["seeded_refunded_orders"] = seeded_refund_ids

    # ── 6. Seed 6 đơn CANCELLED đa dạng lý do (đủ 6 thanh Cancellation stats) ─
    cancel_specs = [
        ("wrong_information", 2, 1, 10, 0.5, "NORMAL", 0),
        ("wrong_information", 3, 2, 9, 0.8, "EXPRESS", 1),
        ("price_too_high", 4, 1, 16, 1.2, "EXPRESS", 2),
        ("found_alternative", 5, 10, 1, 0.6, "NORMAL", 0),
        ("delivery_too_slow", 5, 9, 10, 0.7, "SCHEDULED", 1),
        ("other", 6, 2, 16, 1.0, "NORMAL", 2),
    ]
    seeded_cancelled_ids: list[str] = []
    for reason, days_ago, orig_id, dest_id, kg, dmode, cidx in cancel_specs:
        oh = hubs_map.get(orig_id) or next(iter(hubs_map.values()))
        dh = hubs_map.get(dest_id) or next(iter(hubs_map.values()))
        cust = _pick_customer(cidx)
        c_naive = now_vn - dt.timedelta(days=days_ago, hours=2)
        c_tz = now_tz - dt.timedelta(days=days_ago, hours=2)
        can_naive = c_naive + dt.timedelta(minutes=15)
        can_tz = c_tz + dt.timedelta(minutes=15)
        dist_m = _haversine_m(float(oh["latitude"]), float(oh["longitude"]), float(dh["latitude"]), float(dh["longitude"]))
        fee, _, _ = _compute_demo_delivery_fee(dist_m, kg, "Documents", dmode)
        oid = _next_order_id(c_naive)
        conn.execute(
            text("""
                INSERT INTO orders (
                    id, customer_id, sender_phone, sender_email, receiver_name, receiver_phone,
                    package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                    delivery_mode, requested_delivery_at, estimated_window_start, estimated_window_end,
                    status, created_at, updated_at, cancelled_at,
                    delivery_fee, payment_status, cancellation_reason, is_aging_priority
                ) VALUES (
                    :id, :cust_id, '0909333444', :cust_email, 'Tran Van Khanh', '0909555666',
                    NULL, 'Documents', :kg, :orig, :dest,
                    :mode, :req_at, :win_s, :win_e,
                    'CANCELLED', :c_at, :can_at, :can_at,
                    :fee, 'UNPAID', :rsn, false
                )
            """),
            {
                "id": oid, "cust_id": cust["id"], "cust_email": cust["email"],
                "kg": kg, "orig": oh["id"], "dest": dh["id"], "mode": dmode,
                "req_at": c_naive + dt.timedelta(hours=4),
                "win_s": c_naive + dt.timedelta(hours=1),
                "win_e": c_naive + dt.timedelta(hours=5),
                "c_at": c_naive, "can_at": can_naive, "fee": fee, "rsn": reason,
            },
        )
        conn.execute(
            text("""
                INSERT INTO activity_logs (
                    user_id, user_email, role, action, resource_type, resource_id, description, status, created_at
                ) VALUES (
                    :uid, :uemail, 'CUSTOMER', 'CANCEL_ORDER', 'order', :oid, :desc, 'success', :can_tz
                )
            """),
            {
                "uid": cust["id"], "uemail": cust["email"], "oid": oid,
                "desc": f"Customer cancelled order {oid} (reason: {reason})",
                "can_tz": can_tz,
            },
        )
        conn.execute(
            text("""
                INSERT INTO notifications (
                    user_id, title, message, notifications_type, reference_id, is_read, created_at
                ) VALUES (
                    :uid, :title, :msg, 'Mission_Status', :oid, true, :can_tz
                )
            """),
            {
                "uid": cust["id"], "title": f"Order {oid} cancelled",
                "msg": f"Your order {oid} was cancelled (reason: {reason}).",
                "oid": oid, "can_tz": can_tz,
            },
        )
        seeded_cancelled_ids.append(oid)
    stats["seeded_cancelled_orders"] = seeded_cancelled_ids

    # ── 7. Bật cờ Incident cho chuyến bay demo đầu tiên sau khi clear ─────────
    arm_id = _arm_first_flight_incident_flag(conn, mode="demo-stabilize")
    stats["armed_first_flight_incident_log_id"] = arm_id

    return stats


def run_reset(
    mode: str = "polish",
    execute: bool = False,
    yes: bool = False,
    backup: bool = True,
    keep_saved_addresses: bool = False,
    reactivate_all_accounts: bool = False,
    seed_incident: bool = True,
    incident_origin_hub: int = 16,
    incident_dest_hub: int = 17,
    default_hub_id: int = 1,
) -> None:
    t0 = time.perf_counter()
    clear_tables = [t for t in CLEAR_ORDER if not (keep_saved_addresses and t == "saved_addresses")]

    print(
        f"[{time.perf_counter()-t0:.2f}s] Kết nối Database "
        f"(mode: {mode.upper()} | {'EXECUTE' if execute else 'DRY-RUN'} | seed_incident={seed_incident})...",
        flush=True,
    )
    with engine.connect() as conn:
        before_counts = _fetch_counts(conn, ALL_25_TABLES)
        hub_exists = conn.execute(
            text("SELECT id FROM hubs WHERE id = :hid"), {"hid": default_hub_id}
        ).scalar()
        if hub_exists is None:
            first_hub = conn.execute(text("SELECT id FROM hubs ORDER BY id ASC LIMIT 1")).scalar()
            if first_hub is None:
                raise RuntimeError("Bảng hubs đang trống — dừng lại để tránh làm hỏng dữ liệu đội bay!")
            default_hub_id = int(first_hub)

    if mode == "full":
        _print_summary_table(before_counts, after=None, clear_tables=clear_tables)
    elif mode == "demo-stabilize":
        with engine.connect() as conn:
            ov_preview = [
                r[0]
                for r in conn.execute(
                    text("""
                        SELECT id FROM orders
                        WHERE status = 'AT_HUB'
                          AND estimated_window_end IS NOT NULL
                          AND estimated_window_end < :now_vn
                        ORDER BY id ASC
                    """),
                    {"now_vn": get_vn_naive_datetime()},
                ).fetchall()
            ]
        print(
            "\n[CHẾ ĐỘ DEMO-STABILIZE — MỤC 7 (STABILIZE LIVE DB + TRACEABLE SEED + FIRST-FLIGHT INCIDENT)]"
            "\n  - GIỮ NGUYÊN: Toàn bộ Master Data, 205 Drones, 211+ Pin, 23 missions hoàn thành, ratings, work_orders, replacement_requests."
            f"\n  - XÓA OVERDUE: {len(ov_preview)} đơn AT_HUB quá hạn ({', '.join(ov_preview) if ov_preview else 'không có'})."
            "\n  - SEED AT_HUB: 5 đơn PAID còn hạn dài (2 đơn gộp Ben Thanh -> Thu Duc NORMAL nằm dưới đơn EXPRESS demo, 2 đơn tuyến dài Vung Tau, 1 SCHEDULED)."
            "\n  - SEED PIN   : 4 viên pin mới chưa phân bổ (2x DJI DB2000, 1x Aurelia UltraPower 12S, 1x DJI TB48D) -> Added this week = 4."
            "\n  - SEED REFUND: 3 đơn REFUNDED đủ nguồn gốc (1 sự cố 100%, 1 WEATHER_SAFETY + Open-Meteo, 1 OPERATOR_DECISION + SLA violation)."
            "\n  - SEED FAILED: 2 mission Failed đã đóng kín (Incident Closed, Alert Resolved, WorkOrder Completed, không khóa đội bay)."
            "\n  - SEED CANCEL: 6 đơn CANCELLED đa dạng 5 lý do chuẩn BE -> đủ 6 thanh trên Cancellation Statistics."
            "\n  - FIRST FLIGHT: Tự động bật cờ `ARM_FIRST_FLIGHT_INCIDENT` trong `system_logs` để chuyến bay demo đầu tiên xảy ra DISCONNECT_CRASH (chuyến thứ 2 trở đi 100% SUCCESS).",
            flush=True,
        )
    else:
        print(
            "\n[CHẾ ĐỘ POLISH & TRIM — ZERO-EMPTY CLEAN]"
            "\n  - GIỮ NGUYÊN: 4 In Flight + 6 Maintenance drones, toàn bộ 205 drones & 211 pin (gồm Da Kao & Hiep Binh),"
            "\n                lịch sử orders/missions hoàn thành, work_orders (13/1/2), supply_transactions, activity_logs, ratings."
            "\n  - DỌN GỌN   : Cắt spam notifications (~1,391 -> ~30), đóng bớt incidents/alerts cũ (giữ Open & Closed > 0),"
            "\n                cắt bớt đơn/mission hủy thừa (giữ >= 2), sạc pin rảnh yếu & bổ sung vật tư dưới ngưỡng."
            f"\n  - DEMO SEED : {'BẬT — Thêm 1 mission incident mới tại Hub ' + str(incident_origin_hub) + ' -> Hub ' + str(incident_dest_hub) if seed_incident else 'TẮT'}",
            flush=True,
        )

    if not execute:
        print(
            "\n[DRY-RUN] Không có dữ liệu nào bị thay đổi."
            f"\nĐể thực thi, chạy lại với cờ `--execute`:\n"
            f"  python -u app/shared/database/reset_db_for_defense.py --mode {mode} --execute",
            flush=True,
        )
        return

    if not yes:
        confirm = input(
            f"\nXÁC NHẬN chạy chế độ `{mode.upper()}` trên Live Database (có tự động backup JSON)?\n"
            "Nhập 'RESET' để tiếp tục: "
        ).strip()
        if confirm != "RESET":
            print("Đã hủy thao tác. Database giữ nguyên.", flush=True)
            return

    if backup:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = SCRIPT_DIR / f"backup_before_defense_reset_{ts}.json"
        print(f"\n[{time.perf_counter()-t0:.2f}s] Đang chụp backup JSON trước khi sửa -> {backup_path.name}...", flush=True)
        backup_tables = FLEET_RESET_TABLES + ["hub_supplies", "accounts"] + [t for t in clear_tables if t != "system_logs"]
        with engine.connect() as conn:
            _backup_before_reset(conn, backup_tables, backup_path)
        print(f"[{time.perf_counter()-t0:.2f}s] Backup hoàn tất ({backup_path.stat().st_size / 1024:.1f} KB).", flush=True)

    now_vn = get_vn_naive_datetime()
    print(f"\n[{time.perf_counter()-t0:.2f}s] Bắt đầu Transaction ({mode.upper()})...", flush=True)

    seeded_info = None
    stabilize_stats = None
    with engine.begin() as conn:
        if mode == "demo-stabilize":
            stabilize_stats = _run_demo_stabilize(conn, now_vn)
            for k, v in stabilize_stats.items():
                print(f"  - STABILIZE {k:<32}: {v}", flush=True)
            if reactivate_all_accounts:
                conn.execute(text("UPDATE accounts SET is_active = true WHERE email != 'test@test.com'"))
        elif mode == "polish":
            pstats = _run_polish_cleanup(conn, now_vn)
            for k, v in pstats.items():
                print(f"  - POLISH {k:<28}: {v:>6}", flush=True)
            if reactivate_all_accounts:
                conn.execute(text("UPDATE accounts SET is_active = true WHERE email != 'test@test.com'"))
            if seed_incident:
                seeded_info = _seed_demo_incident_mission(
                    conn,
                    origin_hub_id=incident_origin_hub,
                    dest_hub_id=incident_dest_hub,
                )
                print(
                    f"  - SEED   {'incident_mission':<28}: {seeded_info['mission_id']} "
                    f"({seeded_info['origin_hub']} -> {seeded_info['dest_hub']}, "
                    f"drone={seeded_info['drone_id']}, bat={seeded_info['battery_id']}, "
                    f"tech={seeded_info['dest_tech_email']})",
                    flush=True,
                )
        else:
            # Chế độ FULL: xóa sạch 17 bảng nghiệp vụ & reset 100% đội bay
            conn.execute(text("UPDATE work_orders SET alert_id = NULL WHERE alert_id IS NOT NULL"))
            conn.execute(text("UPDATE maintenance_alerts SET work_order_id = NULL WHERE work_order_id IS NOT NULL"))
            conn.execute(text("UPDATE batteries SET drone_id = NULL, work_order_id = NULL"))

            existing = {
                r[0]
                for r in conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                ).fetchall()
            }
            for tbl in clear_tables:
                if tbl not in existing:
                    continue
                res = conn.execute(text(f'DELETE FROM "{tbl}"'))
                print(f"  - DELETE {tbl:<24}: {res.rowcount:>6} dòng", flush=True)

            for tbl in SERIAL_ID_TABLES:
                if tbl not in clear_tables or tbl not in existing:
                    continue
                seq_name = conn.execute(
                    text("SELECT pg_get_serial_sequence(:tbl, 'id')"), {"tbl": f"public.{tbl}"}
                ).scalar()
                if seq_name:
                    conn.execute(text("SELECT setval(:seq, 1, false)"), {"seq": seq_name})

            # Lưu ý: KHÔNG reset `drones.image_url` / `batteries.image_url` và KHÔNG xóa thư mục `uploads/`
            # để toàn bộ hình ảnh thiết bị & ảnh minh chứng đã lưu trên server luôn được bảo toàn.
            res_drones = conn.execute(
                text("""
                    UPDATE drones
                    SET operational_status = 'Available',
                        battery_level_pct  = 100,
                        utilization_pct    = 0.0,
                        total_flight_hours = 0.0,
                        last_maintenance_at = NULL,
                        current_hub_id     = COALESCE(current_hub_id, :default_hub_id)
                """),
                {"default_hub_id": default_hub_id},
            )
            print(f"  - RESET  {'drones':<24}: {res_drones.rowcount:>6} drone -> Available (100% pin, 0h bay)", flush=True)

            res_bats = conn.execute(
                text("""
                    UPDATE batteries
                    SET status           = 'Active',
                        charge_level_pct = 100,
                        drone_id         = NULL,
                        work_order_id    = NULL,
                        last_charged_at  = :now_vn,
                        current_hub_id   = COALESCE(current_hub_id, :default_hub_id)
                """),
                {"now_vn": now_vn, "default_hub_id": default_hub_id},
            )
            print(f"  - RESET  {'batteries':<24}: {res_bats.rowcount:>6} pin   -> Active (100% pin, đã tháo rời)", flush=True)

            res_sup = conn.execute(
                text("""
                    UPDATE hub_supplies
                    SET quantity   = GREATEST(quantity, min_stock_threshold + 10, 20),
                        updated_at = NOW()
                    WHERE quantity < GREATEST(min_stock_threshold + 10, 20)
                """)
            )
            print(f"  - REFILL {'hub_supplies':<24}: {res_sup.rowcount:>6} dòng vật tư được bổ sung mức tồn kho khỏe", flush=True)

            conn.execute(
                text("""
                    UPDATE accounts
                    SET reset_token = NULL,
                        reset_token_expires_at = NULL
                    WHERE reset_token IS NOT NULL OR reset_token_expires_at IS NOT NULL
                """)
            )
            if reactivate_all_accounts:
                conn.execute(text("UPDATE accounts SET is_active = true WHERE email != 'test@test.com'"))

            if seed_incident:
                seeded_info = _seed_demo_incident_mission(
                    conn,
                    origin_hub_id=incident_origin_hub,
                    dest_hub_id=incident_dest_hub,
                )
                print(
                    f"  - SEED   {'incident_mission':<24}: {seeded_info['mission_id']} "
                    f"({seeded_info['origin_hub']} -> {seeded_info['dest_hub']}, "
                    f"drone={seeded_info['drone_id']}, bat={seeded_info['battery_id']}, "
                    f"tech={seeded_info['dest_tech_email']})",
                    flush=True,
                )
                arm_id = _arm_first_flight_incident_flag(conn, mode="full")
                print(f"  - ARM    {'first_flight_incident':<24}: system_logs.id={arm_id}", flush=True)

    # Post-Reset Health Check (Read-Only)
    print(f"\n[{time.perf_counter()-t0:.2f}s] Đang chạy Post-Reset Health Check...", flush=True)
    with engine.connect() as conn:
        after_counts = _fetch_counts(conn, ALL_25_TABLES)
        drone_breakdown = dict(
            conn.execute(text("SELECT operational_status::text, COUNT(*) FROM drones GROUP BY 1")).fetchall()
        )
        low_sup = conn.execute(
            text("SELECT COUNT(*) FROM hub_supplies WHERE quantity < min_stock_threshold")
        ).scalar()

    _print_summary_table(before_counts, after=after_counts, clear_tables=clear_tables if mode == "full" else [])
    print(f"\n[HEALTH CHECK] drones={drone_breakdown} | low_supplies={low_sup}", flush=True)
    if low_sup:
        raise RuntimeError("Post-Reset Health Check phát hiện hub_supplies vẫn dưới ngưỡng!")

    if seeded_info:
        print(
            "\n[DEMO INCIDENT READY]"
            f"\n  - Order / Mission : {seeded_info['order_id']} / {seeded_info['mission_id']} (Incident #{seeded_info['incident_id']})"
            f"\n  - Tuyến bay       : {seeded_info['origin_hub']} -> {seeded_info['dest_hub']} | GPS rơi: {seeded_info['crash_gps']}"
            f"\n  - Thiết bị khóa   : Drone {seeded_info['drone_id']} (Maintenance) + Pin {seeded_info['battery_id']}"
            f"\n  - Tech xử lý      : {seeded_info['dest_tech_email']} (đăng nhập xem /incoming -> Confirm Arrival -> Work Order)"
            f"\n  - Chuyến bay sau  : Simulator mặc định TẮT incident (100% SUCCESS cho mọi chuyến bay demo tiếp theo).",
            flush=True,
        )

    print(
        f"\n[{time.perf_counter()-t0:.2f}s] HOÀN TẤT ({mode.upper()}) SẴN SÀNG BẢO VỆ ĐỒ ÁN!"
        "\nLưu ý: Nếu backend server đang bật cache RAM (hubs/routes), hãy restart uvicorn sau khi chạy.",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear & reset DroneOptAI database về trạng thái sạch chuẩn bảo vệ đồ án."
    )
    parser.add_argument(
        "--mode",
        choices=("polish", "full", "demo-stabilize"),
        default="polish",
        help="Chế độ dọn DB: 'polish', 'full', hoặc 'demo-stabilize' (Mục 7: xóa overdue, seed dữ liệu demo đầy đủ nguồn gốc & bật cờ incident chuyến bay đầu tiên).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Thực thi thay đổi lên Database (mặc định không truyền là DRY-RUN chỉ đọc).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bỏ qua bước nhập 'RESET' xác nhận thủ công.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Không xuất file JSON backup trước khi xóa.",
    )
    parser.add_argument(
        "--keep-saved-addresses",
        action="store_true",
        help="Giữ lại danh bạ địa chỉ đã lưu của Customer (saved_addresses).",
    )
    parser.add_argument(
        "--reactivate-accounts",
        action="store_true",
        help="Bật lại is_active=true cho toàn bộ tài khoản (trừ test@test.com).",
    )
    parser.add_argument(
        "--no-seed-incident",
        action="store_true",
        help="Không tạo sẵn 1 mission sự cố sau khi clear (để DB trống hoàn toàn 0 mission).",
    )
    parser.add_argument(
        "--origin-hub",
        type=int,
        default=16,
        help="Hub xuất phát cho mission incident mẫu (mặc định: 16 - Vung Tau Hub).",
    )
    parser.add_argument(
        "--dest-hub",
        type=int,
        default=17,
        help="Hub đích / thu hồi cho mission incident mẫu (mặc định: 17 - Tam Thang Hub).",
    )
    args = parser.parse_args()

    try:
        run_reset(
            mode=args.mode,
            execute=args.execute,
            yes=args.yes,
            backup=not args.no_backup,
            keep_saved_addresses=args.keep_saved_addresses,
            reactivate_all_accounts=args.reactivate_accounts,
            seed_incident=not args.no_seed_incident,
            incident_origin_hub=args.origin_hub,
            incident_dest_hub=args.dest_hub,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
