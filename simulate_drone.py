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
BASE_URL   = "https://isp492.onrender.com/api/v1"   # hoặc https://<app>.onrender.com/api/v1
TOKEN      = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjM2U1YjI2Yy0yNTc1LTQzZjQtOTYzOC03Njk5MGM4NzgwYmIiLCJyb2xlIjoiT3BlcmF0b3IiLCJ0eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzkwNDAzMjUxfQ.KtmOx1fRypD_fQ8Vl1y2QKRshnIqOHstqK1l2VMz4eM"
MISSION_ID = 4                                # mission đang ở trạng thái APPROVED
# ─────────────────────────────────────────────────────────────────────────────

INTERVAL_SEC = 2   # gửi mỗi N giây — giảm xuống 2s để animation mượt hơn


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


# ─── FETCH MISSION + LOCATIONS ───────────────────────────────────────────────

def fetch_mission_route(headers) -> tuple[dict, dict] | None:
    """
    Lấy pickup và dropoff location từ mission.
    Trả về (pickup, dropoff) mỗi cái là dict {name, latitude, longitude}
    hoặc None nếu mission không có đủ thông tin.
    """
    # Lấy thông tin mission
    try:
        resp = requests.get(f"{BASE_URL}/operator/missions/{MISSION_ID}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"❌  Không lấy được mission #{MISSION_ID}: HTTP {resp.status_code}")
            return None
        mission = resp.json()
    except Exception as e:
        print(f"❌  Lỗi fetch mission: {e}")
        return None

    pickup_id  = mission.get("pickup_location_id")
    dropoff_id = mission.get("dropoff_location_id")

    print(f"📋  Mission #{MISSION_ID}: pickup_location_id={pickup_id}, dropoff_location_id={dropoff_id}")

    if not pickup_id or not dropoff_id:
        print("⚠️   Mission không có pickup/dropoff location → dùng lộ trình hub fallback")
        return None

    # Fetch tọa độ từng location qua /hubs + /mini-hubs
    all_locations = {}
    for ep in ["/operator/missions/hubs", "/operator/missions/mini-hubs"]:
        try:
            r = requests.get(f"{BASE_URL}{ep}", headers=headers, timeout=10)
            if r.ok:
                for loc in r.json():
                    all_locations[loc["id"]] = loc
        except Exception:
            pass

    pickup  = all_locations.get(pickup_id)
    dropoff = all_locations.get(dropoff_id)

    if not pickup:
        print(f"❌  Không tìm thấy pickup location id={pickup_id} trong DB")
        return None
    if not dropoff:
        print(f"❌  Không tìm thấy dropoff location id={dropoff_id} trong DB")
        return None

    print(f"    📍 Pickup  : {pickup.get('name')} ({pickup['latitude']:.5f}, {pickup['longitude']:.5f})")
    print(f"    🏁 Dropoff : {dropoff.get('name')} ({dropoff['latitude']:.5f}, {dropoff['longitude']:.5f})")
    return pickup, dropoff


def build_route_pickup_to_dropoff(pickup: dict, dropoff: dict, intermediate_hubs: list[dict]) -> list[tuple]:
    """
    Tạo lộ trình từ pickup → (các hub trung gian nằm trên đường) → dropoff.
    Hub trung gian = hub nào nằm trong hành lang 2km giữa pickup và dropoff.
    """
    p_lat, p_lng = pickup["latitude"],  pickup["longitude"]
    d_lat, d_lng = dropoff["latitude"], dropoff["longitude"]

    # Lọc hub trung gian: chỉ lấy hub nằm giữa pickup và dropoff (trong hành lang)
    def is_between(hub):
        h_lat, h_lng = hub["latitude"], hub["longitude"]
        # Khoảng cách từ hub đến đường thẳng pickup→dropoff (cross-track distance đơn giản)
        # Dùng bounding box mở rộng 0.02 độ (~2km)
        min_lat = min(p_lat, d_lat) - 0.02
        max_lat = max(p_lat, d_lat) + 0.02
        min_lng = min(p_lng, d_lng) - 0.02
        max_lng = max(p_lng, d_lng) + 0.02
        return min_lat <= h_lat <= max_lat and min_lng <= h_lng <= max_lng

    # Loại bỏ pickup và dropoff khỏi danh sách hub trung gian
    mid_hubs = [
        h for h in intermediate_hubs
        if is_between(h)
        and not (abs(h["latitude"] - p_lat) < 0.001 and abs(h["longitude"] - p_lng) < 0.001)
        and not (abs(h["latitude"] - d_lat) < 0.001 and abs(h["longitude"] - d_lng) < 0.001)
    ]

    # Sắp xếp hub trung gian theo khoảng cách từ pickup (gần nhất đến xa nhất)
    mid_hubs.sort(key=lambda h: haversine_m(p_lat, p_lng, h["latitude"], h["longitude"]))

    waypoints = [(p_lat, p_lng, f"🛫 {pickup.get('name', 'Pickup')}")]
    for h in mid_hubs:
        waypoints.append((h["latitude"], h["longitude"], f"📍 {h['name']}"))
    waypoints.append((d_lat, d_lng, f"🏁 {dropoff.get('name', 'Dropoff')}"))

    if mid_hubs:
        print(f"\n🗺️   Lộ trình: {pickup.get('name')} → {' → '.join(h['name'] for h in mid_hubs)} → {dropoff.get('name')}")
    else:
        print(f"\n🗺️   Lộ trình thẳng: {pickup.get('name')} → {dropoff.get('name')} (không có hub trung gian)")

    return waypoints


# ─── MAIN SIMULATE ────────────────────────────────────────────────────────────

def simulate():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }

    # 1. Lấy pickup/dropoff từ mission
    print(f"🔍  Đang đọc mission #{MISSION_ID}...")
    route_result = fetch_mission_route(headers)

    # 2. Fetch tất cả hub để dùng làm trung gian
    all_hubs = []
    for ep in ["/operator/missions/hubs", "/operator/missions/mini-hubs"]:
        try:
            r = requests.get(f"{BASE_URL}{ep}", headers=headers, timeout=10)
            if r.ok:
                for h in r.json():
                    if h.get("latitude") and h.get("longitude"):
                        # Chỉ lấy hub trong TP.HCM
                        if 10.60 <= h["latitude"] <= 10.90 and 106.50 <= h["longitude"] <= 107.00:
                            all_hubs.append({
                                "id": h["id"],
                                "name": h.get("name") or h.get("code") or f"Hub #{h['id']}",
                                "latitude": h["latitude"],
                                "longitude": h["longitude"],
                            })
                        else:
                            print(f"⚠️   Bỏ qua hub ngoài TP.HCM: {h.get('name')} ({h.get('latitude')}, {h.get('longitude')})")
        except Exception as e:
            print(f"⚠️  {ep}: {e}")

    # 3. Build lộ trình
    if route_result:
        pickup, dropoff = route_result
        waypoints = build_route_pickup_to_dropoff(pickup, dropoff, all_hubs)
    else:
        # Fallback: bay qua tất cả hub TP.HCM
        if not all_hubs:
            print("⚠️  Không có hub nào trong TP.HCM, dùng lộ trình cứng.")
            waypoints = [
                (10.7769, 106.7009, "🛫 Start - Quận 1"),
                (10.7850, 106.6950, "📍 Midpoint"),
                (10.7980, 106.6850, "🏁 End - Quận 7"),
            ]
        else:
            print(f"\n✅  Tìm thấy {len(all_hubs)} hub trong TP.HCM, bay qua tất cả:")
            for h in all_hubs:
                print(f"    • {h['name']:35s} ({h['latitude']:.5f}, {h['longitude']:.5f})")
            start_lat = all_hubs[0]["latitude"]  - 0.004
            start_lng = all_hubs[0]["longitude"] - 0.004
            end_lat   = all_hubs[-1]["latitude"]  + 0.004
            end_lng   = all_hubs[-1]["longitude"] + 0.004
            waypoints = [(start_lat, start_lng, "🛫 Start")]
            waypoints += [(h["latitude"], h["longitude"], f"📍 {h['name']}") for h in all_hubs]
            waypoints += [(end_lat, end_lng, "🏁 End")]

    route = interpolate_route(waypoints, steps_between=15)
    total = len(route)

    print(f"\n🚁  Bắt đầu giả lập — {total} điểm, mission #{MISSION_ID}")
    print(f"    Server  : {BASE_URL}")
    print(f"    Interval: {INTERVAL_SEC}s/điểm\n")

    api_url = f"{BASE_URL}/operator/missions/{MISSION_ID}/telemetry"

    for idx, (lat, lng, label) in enumerate(route):
        altitude     = 50 + math.sin(idx * 0.3) * 5
        speed        = 12 + math.cos(idx * 0.2) * 3
        battery_volt = max(18.0, 24.0 - idx * 0.04)
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
            resp = requests.post(api_url, json=payload, headers=headers, timeout=10)

            if resp.status_code == 200:
                data   = resp.json()
                status = data.get("mission_status", "?")
                cps    = data.get("new_checkpoints", [])

                label_tag = f"  {label}" if label else ""
                cp_tag = ""
                if cps:
                    names = [c.get("hub_name", "?") for c in cps]
                    dists = [f"{c.get('distance_to_hub_m', 0):.0f}m" for c in cps]
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


if __name__ == "__main__":
    if TOKEN == "PASTE_YOUR_ACCESS_TOKEN_HERE":
        print("❌  Chưa điền TOKEN. Sửa biến TOKEN ở đầu file.")
        exit(1)
    simulate()
