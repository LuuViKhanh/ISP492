"""
seed_demo.py — Script chuẩn bị DB trước demo

Thực hiện (theo thứ tự an toàn, idempotent):
  1. Migrate: thêm cột compensation_pct vào incidents (nếu chưa có)
  2. Clean: xóa orders/missions test rác (PENDING, AT_HUB cũ, missions không có order)
  3. Reset password tất cả users → "123"
  4. Chuẩn hóa email Technician → tech.{code}@droneoptai.com
  5. Cập nhật full_name Technician theo chuẩn
  6. Cập nhật địa chỉ Hub theo chuẩn phường mới TP.HCM
  7. Seed scenario demo đủ loại (completed, cancelled, failed, incident)
  8. Seed 1 case đặc biệt: drone gặp sự cố → mất hàng → đền bù 70%
  9. In bảng credentials để FE test

Chạy: python app/shared/database/seed_demo.py
"""

import sys, os, uuid, random
from datetime import datetime, timedelta

try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
except Exception:
    pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(0, "C:/Users/ext.han.bui/AppData/Roaming/Python/Python314/site-packages")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
import os as _os

# ── Kết nối DB ─────────────────────────────────────────────────────
_raw_url = _os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
if "postgresql" in _raw_url and "+psycopg2" not in _raw_url:
    _raw_url = _raw_url.replace("postgresql://", "postgresql+psycopg2://")
engine = create_engine(_raw_url, echo=False)

# ── Hashing đơn giản dùng bcrypt trực tiếp ─────────────────────────
try:
    from app.modules.auth.service import hash_password
except Exception:
    import bcrypt as _bcrypt
    def hash_password(pw: str) -> str:
        salt = _bcrypt.gensalt()
        return _bcrypt.hashpw(pw.encode("utf-8"), salt).decode("utf-8")

PWD_DEMO = hash_password("123")

# ── Hub data chuẩn phường mới TP.HCM ──────────────────────────────
HUBS = [
    (1,  "HUB-BT1", "Hub Bến Thành",     "1 Lê Lợi, Phường Bến Thành, TP.HCM",                               10.772310, 106.698270),
    (2,  "HUB-VTS", "Hub Võ Văn Tần",    "125 Võ Văn Tần, Phường Võ Thị Sáu, TP.HCM",                        10.777220, 106.686770),
    (3,  "HUB-TP",  "Hub Tân Phong",     "10 Đại lộ Nguyễn Văn Linh, Phường Tân Phong, TP.HCM",              10.729410, 106.718390),
    (4,  "HUB-LC",  "Hub Linh Chiểu",    "371 Võ Văn Ngân, Phường Linh Chiểu, TP.HCM",                       10.850120, 106.772310),
    (5,  "HUB-NO",  "Hub Nguyễn Oanh",   "200 Nguyễn Oanh, Phường 17, Quận Gò Vấp, TP.HCM",                 10.838120, 106.675310),
    (6,  "HUB-UVK", "Hub Ung Văn Khiêm", "100 Ung Văn Khiêm, Phường 25, Quận Bình Thạnh, TP.HCM",           10.812260, 106.711020),
    (7,  "HUB-CH",  "Hub Cộng Hòa",      "15 Cộng Hòa, Phường 4, Quận Tân Bình, TP.HCM",                    10.801430, 106.652190),
    (8,  "HUB-AL",  "Hub An Lạc",        "450 Kinh Dương Vương, Phường An Lạc, Quận Bình Tân, TP.HCM",       10.745530, 106.613420),
    (9,  "HUB-NB",  "Hub Nhà Bè",        "1000 Huỳnh Tấn Phát, Phường Nhà Bè, TP.HCM",                      10.682040, 106.735410),
    (10, "HUB-HT",  "Hub Hòa Thạnh",     "300 Lũy Bán Bích, Phường Hòa Thạnh, Quận Tân Phú, TP.HCM",       10.790020, 106.628010),
]

# email Technician theo phường thực tế (sau sáp nhập quận TP.HCM 2025)
TECH_MAP = {
    1:  {"email": "tech.benthanh@droneoptai.com",   "full_name": "Kỹ thuật viên — Phường Bến Thành"},
    2:  {"email": "tech.vothansu@droneoptai.com",    "full_name": "Kỹ thuật viên — Phường Võ Thị Sáu"},
    3:  {"email": "tech.tanphong@droneoptai.com",    "full_name": "Kỹ thuật viên — Phường Tân Phong"},
    4:  {"email": "tech.linhchieu@droneoptai.com",   "full_name": "Kỹ thuật viên — Phường Linh Chiểu"},
    5:  {"email": "tech.nguyenoanh@droneoptai.com",  "full_name": "Kỹ thuật viên — Phường 17, Gò Vấp"},
    6:  {"email": "tech.ungvankhiem@droneoptai.com", "full_name": "Kỹ thuật viên — Phường 25, Bình Thạnh"},
    7:  {"email": "tech.conghoa@droneoptai.com",     "full_name": "Kỹ thuật viên — Phường 4, Tân Bình"},
    8:  {"email": "tech.anlac@droneoptai.com",       "full_name": "Kỹ thuật viên — Phường An Lạc"},
    9:  {"email": "tech.nhabe@droneoptai.com",       "full_name": "Kỹ thuật viên — Phường Nhà Bè"},
    10: {"email": "tech.hoathanh@droneoptai.com",    "full_name": "Kỹ thuật viên — Phường Hòa Thạnh"},
}


# ════════════════════════════════════════════════════════════════════
def run(conn: Connection):
    print("=" * 60)
    print("  DroneOptAI — SEED DEMO")
    print("=" * 60)

    # ── 1. Migrate: thêm compensation_pct vào incidents ─────────────
    print("\n[1] Migrate: thêm compensation_pct...")
    conn.execute(text("""
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS compensation_pct FLOAT DEFAULT NULL;
    """))
    conn.execute(text("""
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS compensation_note TEXT DEFAULT NULL;
    """))
    print("    OK")

    # ── 2. Clean: xóa dữ liệu test rác ────────────────────────────
    print("\n[2] Clean data...")
    # Xóa notifications rác (giữ nguyên các notification quan trọng)
    conn.execute(text("""
        DELETE FROM notifications
        WHERE title LIKE '%string%' OR user_id IN (
            SELECT id FROM users WHERE full_name = 'string' OR email LIKE '%example.com%'
              AND email NOT LIKE 'tech.%@droneoptai.com'
        )
    """))
    # Xóa orders test không có dữ liệu thực (id 'ORD-001' → 'ORD-010' từ seed_mock)
    conn.execute(text("""
        DELETE FROM orders
        WHERE id ~ '^ORD-0[0-9]{2}$'
    """))
    # Xóa missions test từ seed_massive không có order liên kết
    conn.execute(text("""
        DELETE FROM missions
        WHERE order_id IS NULL AND status = 'Completed'
    """))
    print("    OK")

    # ── 3. Reset password tất cả users → "123" ────────────────────
    print("\n[3] Reset passwords -> '123'...")
    conn.execute(text("UPDATE users SET password_hash = :pwd"), {"pwd": PWD_DEMO})
    print("    OK — tất cả user password = 123")

    # ── 4+5. Chuẩn hóa email + full_name Technician ─────────────
    print("\n[4] Chuẩn hóa email & tên Technician...")
    tech_role_id = conn.execute(text(
        "SELECT id FROM roles WHERE UPPER(role_name) = 'TECHNICIAN' LIMIT 1"
    )).scalar()

    for hub_id, info in TECH_MAP.items():
        new_email = info["email"]
        # Nếu đã tồn tại email mới → chỉ cập nhật full_name
        existing_new = conn.execute(text(
            "SELECT id FROM users WHERE email = :e"
        ), {"e": new_email}).scalar()
        if existing_new:
            conn.execute(text(
                "UPDATE users SET full_name = :fname WHERE email = :e"
            ), {"fname": info["full_name"], "e": new_email})
            continue
        # Cập nhật user theo hub_id + role TECHNICIAN (email cũ dạng tech_hub*)
        conn.execute(text("""
            UPDATE users
            SET email     = :email,
                full_name = :fname
            WHERE id IN (
                SELECT id FROM users
                WHERE hub_id = :hid
                  AND role_id = :rid
                  AND email NOT LIKE 'tech.%@droneoptai.com'
                  AND email != :email
                LIMIT 1
            )
        """), {
            "email": info["email"],
            "fname": info["full_name"],
            "hid":   hub_id,
            "rid":   tech_role_id,
        })
    print("    OK")

    # ── 6. Cập nhật địa chỉ Hub ──────────────────────────────────
    print("\n[6] Cập nhật địa chỉ Hub theo chuẩn phường mới...")
    for hub_id, code, name, address, lat, lng in HUBS:
        conn.execute(text("""
            UPDATE hubs
            SET name    = :name,
                address = :address,
                latitude  = :lat,
                longitude = :lng
            WHERE id = :id
        """), {"id": hub_id, "name": name, "address": address, "lat": lat, "lng": lng})
    print("    OK")

    # ── Lấy IDs cần thiết ────────────────────────────────────────
    cust_id  = conn.execute(text("SELECT id FROM users WHERE email='test@test.com'")).scalar()
    op_id    = conn.execute(text("SELECT id FROM users WHERE email='operator@droneoptai.com'")).scalar()
    tech_ids = conn.execute(text(
        "SELECT id FROM users WHERE role_id = :rid ORDER BY hub_id"
    ), {"rid": tech_role_id}).scalars().all() if tech_role_id else []

    drone_rows = conn.execute(text(
        "SELECT id, current_hub_id FROM drones WHERE operational_status='Available' LIMIT 20"
    )).all()
    drone_map: dict[int, list[str]] = {}
    for d_id, h_id in drone_rows:
        drone_map.setdefault(h_id, []).append(d_id)

    def pick_drone(hub_id):
        avail = drone_map.get(hub_id, [])
        if avail: return avail[0]
        fallback = conn.execute(text("SELECT id FROM drones LIMIT 1")).scalar()
        return fallback

    bat_id = conn.execute(text(
        "SELECT id FROM batteries WHERE status='Active' AND drone_id IS NOT NULL LIMIT 1"
    )).scalar()

    now = datetime.utcnow()

    # ── 7. Seed scenario demo đủ loại ────────────────────────────
    print("\n[7] Seed scenario demo...")

    scenarios = [
        # (label, origin_hub, dest_hub, mode, payload, status_order, status_mission, days_ago, fee)
        ("DEMO-EXPRESS-DONE-1",  1, 3, "EXPRESS",   1.2, "DELIVERED_TO_HUB", "Completed",         3,  85000),
        ("DEMO-EXPRESS-DONE-2",  2, 5, "EXPRESS",   0.8, "DELIVERED_TO_HUB", "Completed",         5, 72000),
        ("DEMO-SCHED-DONE-1",    3, 6, "SCHEDULED", 2.5, "DELIVERED_TO_HUB", "Completed",         7, 120000),
        ("DEMO-SCHED-DONE-2",    4, 7, "SCHEDULED", 3.0, "DELIVERED_TO_HUB", "Completed",         8, 140000),
        ("DEMO-NORMAL-DONE-1",   5, 8, "NORMAL",    1.8, "DELIVERED_TO_HUB", "Completed",         4, 95000),
        ("DEMO-CANCELLED-1",     1, 9, "EXPRESS",   1.0, "CANCELLED",        "Cancelled",         2,  0),
        ("DEMO-CANCELLED-2",     6, 2, "SCHEDULED", 2.0, "CANCELLED",        "Cancelled",         6,  0),
        ("DEMO-FLYING-NOW",      3, 4, "EXPRESS",   1.5, "IN_DELIVERY",      "Flying",            0, 90000),
        ("DEMO-SCHEDULED-UP",    2, 7, "SCHEDULED", 3.5, "IN_DELIVERY",      "Scheduled",         0, 135000),
        ("DEMO-AT-HUB-WAIT",     5, 1, "NORMAL",    2.2, "AT_HUB",           None,                1,  0),
        ("DEMO-AT-HUB-WAIT2",    8, 3, "EXPRESS",   0.9, "AT_HUB",           None,                0,  0),
    ]

    for label, o_hub, d_hub, mode, kg, o_status, m_status, days, fee in scenarios:
        # Bỏ qua nếu đã tồn tại
        exists = conn.execute(text("SELECT 1 FROM orders WHERE id=:id"), {"id": label}).scalar()
        if exists:
            continue

        ago = now - timedelta(days=days, hours=random.randint(0, 8))
        window_start = ago + timedelta(hours=1)
        window_end   = ago + timedelta(hours=2)

        conn.execute(text("""
            INSERT INTO orders (id, customer_id, sender_phone, receiver_name, receiver_phone,
                package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                delivery_mode, estimated_window_start, estimated_window_end,
                status, delivery_fee, payment_status, created_at, updated_at)
            VALUES (:id, :cid, '0901234567', 'Nguyen Van A', '0987654321',
                :label, 'Standard', :kg, :oh, :dh,
                :mode, :ws, :we,
                :ostatus, :fee, 'PAID', :ago, :ago)
        """), {
            "id": label, "cid": cust_id, "label": label, "kg": kg,
            "oh": o_hub, "dh": d_hub, "mode": mode,
            "ws": window_start, "we": window_end,
            "ostatus": o_status, "fee": fee if fee else None, "ago": ago,
        })

        if m_status:
            d_id = pick_drone(o_hub)
            dist = round(random.uniform(6000, 18000), 0)
            start_t = ago + timedelta(minutes=30)
            end_t   = ago + timedelta(hours=1, minutes=30) if m_status in ("Completed", "Mission completed") else None
            arr_t   = end_t

            conn.execute(text("""
                INSERT INTO missions (id, order_id, customer_id, operator_id, drone_id, battery_id,
                    payload_weight, distance_m, delivery_fee,
                    status, scheduled_time, start_time, end_time,
                    origin_hub_id, destination_hub_id,
                    departed_at, arrived_at, arrival_confirmed_at)
                VALUES (:mid, :oid, :cid, :opid, :did, :bid,
                    :wt, :dist, :fee,
                    :mstatus, :sched, :start, :end,
                    :oh, :dh,
                    :dep, :arr, :arrconf)
                ON CONFLICT (id) DO NOTHING
            """), {
                "mid": f"MSN-{label}", "oid": label,
                "cid": cust_id, "opid": op_id,
                "did": d_id, "bid": bat_id,
                "wt": kg, "dist": dist, "fee": fee if fee else None,
                "mstatus": m_status,
                "sched": ago, "start": start_t if m_status != "Scheduled" else None,
                "end": end_t, "oh": o_hub, "dh": d_hub,
                "dep": start_t if m_status != "Scheduled" else None,
                "arr": arr_t,
                "arrconf": arr_t if m_status == "Completed" else None,
            })

    print(f"    OK — {len(scenarios)} scenario")

    # ── 8. Seed case đặc biệt: drone sự cố → mất hàng → đền bù 70% ──
    print("\n[8] Seed case sự cố / mất hàng / đền bù 70%...")

    INCIDENT_ORDER = "DEMO-INCIDENT-LOSS"
    INCIDENT_MSN   = "MSN-DEMO-INCIDENT-LOSS"

    if not conn.execute(text("SELECT 1 FROM orders WHERE id=:id"), {"id": INCIDENT_ORDER}).scalar():
        ago_inc = now - timedelta(days=2, hours=3)
        conn.execute(text("""
            INSERT INTO orders (id, customer_id, sender_phone, receiver_name, receiver_phone,
                package_label, package_type, payload_kg, origin_hub_id, destination_hub_id,
                delivery_mode, estimated_window_start, estimated_window_end,
                status, delivery_fee, payment_status, created_at, updated_at)
            VALUES (:id, :cid, '0909111222', 'Tran Thi B', '0978888777',
                'Laptop Dell XPS 15 (dễ vỡ)', 'Fragile', 2.1, 1, 9,
                'EXPRESS',
                :ws, :we,
                'CANCELLED', 195000, 'PAID', :ago, :ago)
        """), {
            "id": INCIDENT_ORDER, "cid": cust_id,
            "ws": ago_inc + timedelta(hours=1),
            "we": ago_inc + timedelta(hours=2),
            "ago": ago_inc,
        })

        d_inc = pick_drone(1)
        conn.execute(text("""
            INSERT INTO missions (id, order_id, customer_id, operator_id, drone_id, battery_id,
                payload_weight, distance_m, delivery_fee,
                status, scheduled_time, start_time,
                origin_hub_id, destination_hub_id, departed_at)
            VALUES (:mid, :oid, :cid, :opid, :did, :bid,
                2.1, 32500, 195000,
                'Incident return', :sched, :start,
                1, 9, :dep)
            ON CONFLICT (id) DO NOTHING
        """), {
            "mid": INCIDENT_MSN, "oid": INCIDENT_ORDER,
            "cid": cust_id, "opid": op_id,
            "did": d_inc, "bid": bat_id,
            "sched": ago_inc,
            "start": ago_inc + timedelta(minutes=20),
            "dep":   ago_inc + timedelta(minutes=20),
        })

        # Tạo Incident
        comp_date_str = (ago_inc + timedelta(days=1)).strftime("%d/%m/%Y")
        comp_note = (
            f"Den bu 70% gia tri don hang (195,000 VND) = 136,500 VND. "
            f"Mien 30% theo dieu khoan 8.3 Chinh sach Rui ro Bay: "
            f"su co do gio giat ngoai tam kiem soat (Beaufort 7+). "
            f"Khach hang da xac nhan nhan den bu ngay {comp_date_str}."
        )
        inc_desc = (
            "Drone mat tin hieu dot ngot tai toa do 10.72N/106.71E, do cao 45m. "
            "Drone ha canh khan cap xuong vung khong kiem soat. "
            "Kien hang (Laptop Dell XPS 15 Fragile) khong the thu hoi - xac nhan MAT HANG."
        )
        inc_result = conn.execute(text("""
            INSERT INTO incidents (mission_id, drone_id, reporter_id, severity, description,
                status, reported_at, requires_technical_inspection,
                compensation_pct, compensation_note)
            VALUES (:mid, :did, :rid, 'High',
                :desc,
                'Closed', :reported, true,
                70.0, :comp_note)
            RETURNING id
        """), {
            "mid":       INCIDENT_MSN,
            "did":       d_inc,
            "rid":       op_id,
            "desc":      inc_desc,
            "reported":  ago_inc + timedelta(minutes=45),
            "comp_note": comp_note,
        }).scalar()

        # Notification cho Customer về đền bù
        if cust_id:
            conn.execute(text("""
                INSERT INTO notifications (user_id, title, message, notifications_type,
                    reference_id, is_read, created_at, mission_id)
                VALUES (:uid,
                    '⚠️ Sự cố chuyến bay — Thông báo đền bù',
                    'Chuyến bay MSN-DEMO-INCIDENT-LOSS gặp sự cố nghiêm trọng. '
                    'Drone mất tín hiệu và hàng hóa không thể thu hồi. '
                    'Theo chính sách bảo hiểm, bạn được đền bù 70% = 136,500 VNĐ. '
                    'Vui lòng liên hệ bộ phận hỗ trợ để hoàn tất thủ tục.',
                    'Mission_Status',
                    :ref, false, :ts, :mid)
            """), {
                "uid": cust_id,
                "ref": f"INCIDENT_{INCIDENT_MSN}",
                "ts":  ago_inc + timedelta(hours=1),
                "mid": INCIDENT_MSN,
            })

        print(f"    OK — Incident ID #{inc_result}, compensation_pct=70%")
    else:
        print("    SKIP — case đã tồn tại")

    # ── 9. In credentials ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  CREDENTIALS (password = 123 cho tất cả)")
    print("=" * 60)

    creds = conn.execute(text("""
        SELECT u.email, u.full_name, r.role_name, u.hub_id
        FROM users u
        JOIN roles r ON u.role_id = r.id
        WHERE u.email NOT LIKE '%string%'
          AND u.email NOT LIKE 'user@%'
          AND u.email NOT LIKE 'oper1@%'
          AND u.email NOT LIKE 'admin1@%'
          AND u.email NOT LIKE 'tech1@%'
          AND u.email NOT LIKE 'lala%'
        ORDER BY r.role_name, u.email
    """)).all()

    last_role = ""
    for email, name, role, hub in creds:
        if role != last_role:
            print(f"\n  [{role.upper()}]")
            last_role = role
        hub_str = f" (hub_id={hub})" if hub else ""
        print(f"    {email:45s}  {name}{hub_str}")

    print("\n  Base URL: https://droneoptai.id.vn/api/v1")
    print("=" * 60)


# ── MAIN ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    with engine.connect() as conn:
        with conn.begin():
            run(conn)
    print("\nDone. DB sẵn sàng cho demo!")
