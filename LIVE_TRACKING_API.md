# Live Tracking API — Hướng dẫn tích hợp Frontend

Base URL: `https://isp492.onrender.com/api/v1`  
Auth: `Authorization: Bearer <operator_token>`

---

## 1. Các endpoint cần dùng

### `GET /operator/missions/live-tracking`
> Poll mỗi **3 giây** để cập nhật vị trí tất cả drone đang bay.

**Response:**
```json
{
  "active_count": 2,
  "drones": [
    {
      "mission_id": 4,
      "drone_id": 15,
      "status": "Flying",
      "latest_lat": 10.77231,
      "latest_lng": 106.69827,
      "latest_altitude": 52.3,
      "latest_speed": 13.1,
      "latest_battery_voltage": 22.4,
      "last_updated": "2026-09-25T10:00:03",
      "checkpoints_passed": 2
    }
  ]
}
```

**Dùng để:** Di chuyển marker drone trên map mỗi 3s.

---

### `GET /operator/missions/{mission_id}/telemetry?limit=2000`
> Lấy toàn bộ lịch sử tọa độ của một chuyến bay.

**Response:** Array các điểm tọa độ theo thứ tự thời gian.
```json
[
  {
    "id": 1,
    "mission_id": 4,
    "timestamp": "2026-09-25T10:00:01",
    "latitude": 10.77231,
    "longitude": 106.69827,
    "altitude": 50.0,
    "speed": 12.0,
    "battery_voltage": 24.0,
    "energy_consumed_wh": 0.0,
    "wind_speed": 2.1
  }
]
```

**Dùng để:** Vẽ polyline đường bay đã qua.

---

### `GET /operator/missions/{mission_id}/hub-checkpoints`
> Danh sách các hub trung gian drone đã đi qua.

**Response:**
```json
[
  {
    "id": 1,
    "mission_id": 4,
    "hub_id": 3,
    "hub_name": "Hub Quận 1 - Bến Thành",
    "hub_latitude": 10.77231,
    "hub_longitude": 106.69827,
    "drone_latitude": 10.77229,
    "drone_longitude": 106.69831,
    "distance_to_hub_m": 4.2,
    "passed_at": "2026-09-25T10:00:05",
    "checkpoint_order": 1
  }
]
```

**Dùng để:** Hiện marker hub + log hành trình.

---

### `GET /operator/missions/hubs`
> Danh sách tất cả hub (hiện marker trên map lúc load).

```json
[
  {
    "id": 3,
    "code": "HUB-Q1",
    "name": "Hub Quận 1 - Bến Thành",
    "latitude": 10.77231,
    "longitude": 106.69827,
    "status": "Active"
  }
]
```

---

## 2. Flow tích hợp map

```
1. Khi vào trang:
   GET /hubs → hiện tất cả hub marker (🏠)

2. setInterval mỗi 3s:
   GET /live-tracking → cập nhật vị trí marker drone (🚁)
   → animate marker di chuyển mượt (interpolation)

3. Khi drone mới xuất hiện hoặc mỗi 10s:
   GET /{mission_id}/telemetry → vẽ polyline đường đã bay

4. Khi click vào drone:
   GET /{mission_id}/hub-checkpoints → hiện checkpoint log
   → zoom fit bounds của polyline
```

---

## 3. Cách vẽ đường như Grab

```javascript
// Đường lịch sử (mờ)
const historyLine = L.polyline(coords, {
  color: 'rgba(129,140,248,0.35)',
  weight: 3,
});

// Đường trail sáng (15 điểm cuối, animated dash)
const tail = coords.slice(-15);
const trailLine = L.polyline(tail, {
  color: '#818cf8',
  weight: 5,
  className: 'trail-line', // CSS animation
});
```

CSS animation cho dash chạy:
```css
.trail-line {
  stroke-dasharray: 8 6;
  animation: dash 1s linear infinite;
}
@keyframes dash {
  to { stroke-dashoffset: -14; }
}
```

---

## 4. Thư viện gợi ý

| Thư viện | Use case |
|---|---|
| **Leaflet.js** | Map nhẹ, dễ tích hợp, miễn phí |
| **Mapbox GL JS** | Đẹp hơn, có 3D, cần token (free tier 50k/tháng) |
| **React Leaflet** | Wrapper cho React |

Tile provider: dùng **Mapbox** với token `pk.eyJ1IjoibWF4Y2FyMjAxOSIs...`

---

## 5. File demo có sẵn trong repo

| File | Mô tả |
|---|---|
| `drone_live_map.html` | Standalone HTML demo, mở bằng browser, implement đầy đủ tất cả tính năng |
| `simulate_drone.py` | Script Python giả lập drone để test khi không có hardware |

FE đọc `drone_live_map.html` để tham khảo logic JS animate marker, vẽ polyline, poll API.
