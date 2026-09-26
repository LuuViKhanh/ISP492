"""
simulate_drone.py
─────────────────
Giả lập một drone đang bay — gửi telemetry lên API (local hoặc Render)
mỗi vài giây theo một lộ trình waypoint định sẵn.

Cách chạy:
    python simulate_drone.py

Tuỳ chỉnh:
    BASE_URL   → đổi sang URL Render của bạn để test production
    TOKEN      → access_token của tài khoản operator/admin
    MISSION_ID → ID của mission đang ở trạng thái APPROVED
"""

import time
import math
import requests
from datetime import datetime, timezone

# ─── CẤU HÌNH ────────────────────────────────────────────────────────────────
BASE_URL   = "http://localhost:8000/api/v1"   # ← đổi thành https://<app>.onrender.com/api/v1
TOKEN      = "PASTE_YOUR_ACCESS_TOKEN_HERE"
MISSION_ID = 1                                 # ← đổi thành mission_id thực tế (status APPROVED)

INTERVAL_SEC    = 3      # Gửi mỗi N giây
POINTS_PER_CALL = 1      # Số điểm gửi mỗi lần (batch)
HUB_PROXIMITY_M = 200    # Phải khớp với config backend (để biết khi nào checkpoint được tạo)
# ─────────────────────────────────────────────────────────────────────────────


# ─── LỘ TRÌNH MẪU (TP.HCM) ───────────────────────────────────────────────────
# Mỗi tuple: (latitude, longitude, tên mốc)
# Lộ trình: Quận 1 → qua Hub Quận 3 → Hub Bình Thạnh → Quận 7
# Thay lat/lng bằng toạ độ hub thực tế trong DB của bạn để trigger checkpoint
WAYPOINTS = [
    (10.7769, 106.7009, "Start - Quận 1"),
    (10.7790, 106.6990, "..."),
    (10.7810, 106.6975, "..."),
    (10.7830, 106.6960, "Gần Hub Quận 3"),        # ← đặt gần lat/lng hub trong DB
    (10.7850, 106.6945, "..."),
    (10.7870, 106.6930, "..."),
    (10.7900, 106.6910, "Gần Hub Bình Thạnh"),     # ← đặt gần lat/lng hub trong DB
    (10.7920, 106.6895, "..."),
    (10.7940, 106.6880, "..."),
    (10.7960, 106.6865, "..."),
    (10.7980, 106.6850, "End - Quận 7"),
]
# ─────────────────────────────────────────────────────────────────────────────


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def interpolate_route(waypoints, steps_between=5):
    """Nội suy thêm điểm giữa các waypoint để đường bay mịn hơn."""
    route = []
    for i in range(len(waypoints) - 1):
        lat1, lon1, label1 = waypoints[i]
        lat2, lon2, _      = waypoints[i + 1]
        for step in range(steps_between):
            t = step / steps_between
            route.append((
                lat1 + (lat2 - lat1) * t,
                lon1 + (lon2 - lon1) * t,
                label1 if step == 0 else "",
            ))
    route.append(waypoints[-1])
    return route


def simulate():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}/operator/missions/{MISSION_ID}/telemetry"

    route = interpolate_route(WAYPOINTS, steps_between=8)
    total = len(route)

    print(f"🚁  Bắt đầu giả lập drone — {total} điểm, mission #{MISSION_ID}")
    print(f"    Server: {BASE_URL}")
    print(f"    Gửi mỗi {INTERVAL_SEC}s\n")

    for idx, (lat, lng, label) in enumerate(route):
        # Tốc độ và altitude thay đổi nhẹ theo thời gian
        altitude      = 50 + math.sin(idx * 0.3) * 5          # 45–55m
        speed         = 12 + math.cos(idx * 0.2) * 3          # 9–15 m/s
        battery_volt  = max(18.0, 24.0 - idx * 0.05)          # giảm dần
        energy_wh     = idx * 0.8                              # tăng dần
        wind_speed    = 2.0 + math.sin(idx * 0.5) * 1.5       # 0.5–3.5 m/s

        payload = [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latitude":          round(lat, 6),
            "longitude":         round(lng, 6),
            "altitude":          round(altitude, 2),
            "speed":             round(speed, 2),
            "battery_voltage":   round(battery_volt, 2),
            "energy_consumed_wh":round(energy_wh, 3),
            "wind_speed":        round(wind_speed, 2),
        }]

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                status  = data.get("mission_status", "?")
                saved   = data.get("saved_count", 0)
                cps     = data.get("new_checkpoints", [])

                tag = f"  📍 {label}" if label else ""
                cp_tag = ""
                if cps:
                    names = [c.get("hub_name", "?") for c in cps]
                    cp_tag = f"  ✅ CHECKPOINT: {', '.join(names)}"

                print(f"[{idx+1:>3}/{total}] ({lat:.5f}, {lng:.5f})  "
                      f"alt={altitude:.1f}m  spd={speed:.1f}m/s  "
                      f"bat={battery_volt:.1f}V  status={status}"
                      f"{tag}{cp_tag}")
            else:
                print(f"[{idx+1:>3}/{total}] ❌ HTTP {resp.status_code}: {resp.text[:120]}")

        except requests.exceptions.ConnectionError:
            print(f"[{idx+1:>3}/{total}] ❌ Không kết nối được tới {BASE_URL}")
            break
        except requests.exceptions.Timeout:
            print(f"[{idx+1:>3}/{total}] ⏱  Timeout, thử lại lần sau")

        if idx < total - 1:
            time.sleep(INTERVAL_SEC)

    print("\n🏁  Giả lập hoàn thành.")


if __name__ == "__main__":
    if TOKEN == "PASTE_YOUR_ACCESS_TOKEN_HERE":
        print("❌  Chưa điền TOKEN. Mở file này và sửa biến TOKEN ở đầu file.")
        exit(1)
    simulate()
