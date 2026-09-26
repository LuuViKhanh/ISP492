"""
simulate_drone.py
─────────────────
Giả lập drone bay — tự fetch hub từ API, tự tạo lộ trình đi QUA các hub,
rồi stream telemetry lên server mỗi vài giây.

Cách chạy:
    python simulate_drone.py

Chỉ cần sửa 3 dòng trong phần CẤU HÌNH bên dưới.
"""

import time
import math
import requests
from datetime import datetime, timezone

# ─── CẤU HÌNH (chỉ sửa 3 dòng này) ──────────────────────────────────────────
BASE_URL   = "http://localhost:8000/api/v1"   # hoặc https://<app>.onrender.com/api/v1
TOKEN      = "PASTE_YOUR_ACCESS_TOKEN_HERE"
MISSION_ID = 1                                # mission đang ở trạng thái APPROVED
# ─────────────────────────────────────────────────────────────────────────────

INTERVAL_SEC = 3   # gửi mỗi N giây


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def interpolate_route(waypoints, steps_between=8):
    """Nội suy điểm giữa các waypoint để đường bay mịn hơn."""
    route = []
    for i in range(len(waypoints) - 1):
        lat1, lon1, label1 = waypoints[i]
        lat2, lon2, _ = waypoints[i + 1]
        for step in range(steps_between):
            t = step / steps_between
            route.append((
                lat1 + (lat2 - lat1) * t,
                lon1 + (lon2 - lon1) * t,
                label1 if step == 0 else "",
            ))
    route.append(waypoints[-1])
    return route


# ─── FETCH HUB TỪ API ─────────────────────────────────────────────────────────

def fetch_hubs(headers) -> list[dict]:
    """
    Lấy danh sách hub thực tế từ DB qua API.
    Gộp cả Hub lớn (/hubs) và Mini-hub (/mini-hubs).
    Trả về list dict: { name, latitude, longitude }
    """
    hubs = []
    for endpoint in ["/operator/missions/hubs", "/operator/missions/mini-hubs"]:
        try:
            resp = requests.get(f"{BASE_URL}{endpoint}", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for h in data:
                    lat = h.get("latitude")
                    lng = h.get("longitude")
                    if lat and lng:
                        hubs.append({
                            "name": h.get("name") or h.get("code") or f"Hub #{h.get('id')}",
                            "latitude": lat,
                            "longitude": lng,
                        })
        except Exception as e:
            print(f"⚠️  Không fetch được {endpoint}: {e}")
    return hubs


def build_route_through_hubs(hubs: list[dict]) -> list[tuple]:
    """
    Tạo lộ trình đi qua TẤT CẢ hub theo thứ tự.

    Logic:
    - Điểm xuất phát = toạ độ hub đầu tiên (dịch nhẹ 0.001 độ để không trùng)
    - Waypoint = toạ độ chính xác của mỗi hub (đảm bảo trong 200m → trigger checkpoint)
    - Điểm kết thúc = toạ độ hub cuối (dịch nhẹ)
    """
    if not hubs:
        # Fallback nếu DB không có hub nào
        print("⚠️  Không tìm thấy hub nào trong DB. Dùng lộ trình mặc định TP.HCM.")
        return [
            (10.7769, 106.7009, "Start (fallback)"),
            (10.7850, 106.6950, "Midpoint"),
            (10.7980, 106.6850, "End (fallback)"),
        ]

    waypoints = []

    # Điểm xuất phát: trước hub đầu tiên ~500m về phía tây-nam
    start_lat = hubs[0]["latitude"] - 0.004
    start_lng = hubs[0]["longitude"] - 0.004
    waypoints.append((start_lat, start_lng, "🛫 Điểm xuất phát"))

    # Đi qua từng hub (toạ độ chính xác → chắc chắn trigger checkpoint)
    for h in hubs:
        waypoints.append((h["latitude"], h["longitude"], f"📍 {h['name']}"))

    # Điểm kết thúc: sau hub cuối ~500m về phía đông-bắc
    end_lat = hubs[-1]["latitude"] + 0.004
    end_lng = hubs[-1]["longitude"] + 0.004
    waypoints.append((end_lat, end_lng, "🏁 Điểm đến"))

    return waypoints


# ─── MAIN SIMULATE ────────────────────────────────────────────────────────────

def simulate():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }

    # 1. Fetch hub từ DB
    print("🔍  Đang fetch danh sách Hub từ DB...")
    hubs = fetch_hubs(headers)

    if hubs:
        print(f"✅  Tìm thấy {len(hubs)} hub:")
        for h in hubs:
            print(f"    • {h['name']:30s} ({h['latitude']:.5f}, {h['longitude']:.5f})")
    else:
        print("⚠️  Không có hub nào — dùng lộ trình fallback")

    print()

    # 2. Build lộ trình đi qua các hub
    waypoints = build_route_through_hubs(hubs)
    route = interpolate_route(waypoints, steps_between=10)
    total = len(route)

    print(f"🚁  Bắt đầu giả lập — {total} điểm, mission #{MISSION_ID}")
    print(f"    Server  : {BASE_URL}")
    print(f"    Interval: {INTERVAL_SEC}s/điểm\n")

    url = f"{BASE_URL}/operator/missions/{MISSION_ID}/telemetry"

    for idx, (lat, lng, label) in enumerate(route):
        altitude     = 50 + math.sin(idx * 0.3) * 5       # 45–55m
        speed        = 12 + math.cos(idx * 0.2) * 3       # 9–15 m/s
        battery_volt = max(18.0, 24.0 - idx * 0.04)       # giảm dần
        energy_wh    = idx * 0.8
        wind_speed   = 2.0 + math.sin(idx * 0.5) * 1.5

        payload = [{
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "latitude":           round(lat, 6),
            "longitude":          round(lng, 6),
            "altitude":           round(altitude, 2),
            "speed":              round(speed, 2),
            "battery_voltage":    round(battery_volt, 2),
            "energy_consumed_wh": round(energy_wh, 3),
            "wind_speed":         round(wind_speed, 2),
        }]

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)

            if resp.status_code == 200:
                data   = resp.json()
                status = data.get("mission_status", "?")
                cps    = data.get("new_checkpoints", [])

                label_tag = f"  {label}" if label else ""
                cp_tag = ""
                if cps:
                    names  = [c.get("hub_name", "?") for c in cps]
                    dists  = [f"{c.get('distance_to_hub_m', 0):.0f}m" for c in cps]
                    cp_tag = f"  ✅ CHECKPOINT: {', '.join(f'{n} ({d})' for n, d in zip(names, dists))}"

                print(
                    f"[{idx+1:>3}/{total}] ({lat:.5f}, {lng:.5f})"
                    f"  alt={altitude:.1f}m  spd={speed:.1f}m/s"
                    f"  bat={battery_volt:.1f}V  [{status}]"
                    f"{label_tag}{cp_tag}"
                )
            else:
                print(f"[{idx+1:>3}/{total}] ❌ HTTP {resp.status_code}: {resp.text[:150]}")

        except requests.exceptions.ConnectionError:
            print(f"[{idx+1:>3}/{total}] ❌ Không kết nối được tới {BASE_URL}")
            break
        except requests.exceptions.Timeout:
            print(f"[{idx+1:>3}/{total}] ⏱  Timeout")

        if idx < total - 1:
            time.sleep(INTERVAL_SEC)

    print("\n🏁  Giả lập hoàn thành.")
    print(f"\n👉  Kiểm tra kết quả:")
    print(f"    GET {BASE_URL}/operator/missions/{MISSION_ID}/hub-checkpoints")
    print(f"    GET {BASE_URL}/operator/missions/{MISSION_ID}/telemetry")
    print(f"    GET {BASE_URL}/operator/missions/live-tracking")


if __name__ == "__main__":
    if TOKEN == "PASTE_YOUR_ACCESS_TOKEN_HERE":
        print("❌  Chưa điền TOKEN. Sửa biến TOKEN ở đầu file.")
        exit(1)
    simulate()
