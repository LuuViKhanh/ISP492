# Live Tracking API — Hướng dẫn tích hợp Frontend

> Base URL: `https://isp492.onrender.com/api/v1`  
> Auth: `Authorization: Bearer <operator_token>` (Operator role)

---

## Tổng quan flow

```
Technician bấm Dispatch
    └─ Backend tự động giả lập drone bay theo MissionLeg đã có trong DB
         └─ Mỗi vài giây insert 1 điểm TelemetryLog (lat/lng nội suy giữa 2 hub)

FE polling GET /operator/missions/live-tracking mỗi 3–5s
    └─ Cập nhật vị trí marker drone trên map

FE click vào drone
    └─ GET /{mission_id}/telemetry  → vẽ trail (đường đã bay)
    └─ GET /{mission_id}/hub-checkpoints → hub đã qua
```

---

## 1. GET `/operator/missions/live-tracking`

**Mô tả:** Trả snapshot tất cả drone đang bay (`status = Flying`). Poll mỗi **3–5 giây**.

**Response:**
```json
{
  "active_count": 2,
  "drones": [
    {
      "mission_id": "MSN-20261007-0003",
      "drone_id": 28,
      "status": "Flying",
      "latest_lat": 10.776900,
      "latest_lng": 106.700900,
      "latest_altitude": 52.3,
      "latest_speed": 13.2,
      "latest_battery_voltage": 22.4,
      "last_updated": "2026-10-18T10:23:45",
      "checkpoints_passed": 1,
      "weather_temperature": 31.2,
      "weather_apparent_temp": 35.1,
      "weather_dew_point": 23.1,
      "weather_humidity": 78.0,
      "weather_wind_speed": 3.2,
      "weather_wind_gust": 5.1,
      "weather_wind_direction": 180.0,
      "weather_precipitation": 0.0,
      "weather_pressure": 1009.0,
      "weather_cloud_cover": 40.0
    }
  ]
}
```

**Ghi chú:**
- `active_count = 0` và `drones = []` khi không có drone bay — không cần xử lý lỗi
- `last_updated` là thời điểm nhận điểm telemetry gần nhất — dùng để phát hiện mất tín hiệu phía FE (nếu > 10s chưa cập nhật thì hiện cảnh báo)
- Tất cả `weather_*` fields có thể `null` nếu Open-Meteo API không phản hồi

**Polling example:**
```js
const interval = setInterval(async () => {
  const res = await fetch('/api/v1/operator/missions/live-tracking', { headers })
  const { drones } = await res.json()
  drones.forEach(drone => updateMarker(drone))
}, 4000)
```

---

## 2. GET `/operator/missions/{mission_id}/telemetry`

**Mô tả:** Lịch sử tọa độ của 1 chuyến bay để vẽ trail.

**Query params:**
| Param | Type | Default | Mô tả |
|---|---|---|---|
| `limit` | int | 1000 | Số điểm tối đa (1–5000) |
| `offset` | int | 0 | Bỏ qua N điểm đầu |

**Response:** Array điểm tọa độ, sorted `timestamp ASC`
```json
[
  {
    "id": 1012,
    "mission_id": "MSN-20261007-0003",
    "timestamp": "2026-10-18T10:20:01",
    "latitude": 10.776500,
    "longitude": 106.700200,
    "altitude": 50.0,
    "speed": 12.5,
    "battery_voltage": 23.1,
    "energy_consumed_wh": 12.4,
    "wind_speed": 2.1,
    "weather_temperature": 31.0,
    "weather_humidity": 78.0,
    "weather_wind_speed": 3.1,
    "weather_wind_gust": 5.0,
    "weather_wind_direction": 175.0,
    "weather_precipitation": 0.0,
    "weather_pressure": 1009.0,
    "weather_cloud_cover": 35.0,
    "weather_apparent_temp": 34.8,
    "weather_dew_point": 23.0
  }
]
```

**Ghi chú:**
- Dùng trực tiếp để vẽ Polyline: `coords = data.map(p => [p.latitude, p.longitude])`
- Lấy 50 điểm gần nhất để vẽ trail ngắn: `?limit=50` rồi sort desc ở FE, hoặc lấy full rồi `.slice(-50)`

---

## 3. GET `/operator/missions/{mission_id}/hub-checkpoints`

**Mô tả:** Danh sách hub drone đã bay qua, sorted theo `checkpoint_order ASC`.

**Response:**
```json
[
  {
    "id": 5,
    "mission_id": "MSN-20261007-0003",
    "hub_id": 2,
    "hub_name": "Hub Quận 7",
    "hub_latitude": 10.732500,
    "hub_longitude": 106.721800,
    "drone_latitude": 10.732300,
    "drone_longitude": 106.721600,
    "distance_to_hub_m": 24.5,
    "passed_at": "2026-10-18T10:21:30",
    "checkpoint_order": 1
  }
]
```

**Ghi chú:**
- Checkpoint được tự động tạo khi drone vào vùng **200m** quanh hub
- `drone_latitude/longitude` là vị trí thực tế khi trigger, không phải tọa độ hub
- Dùng để render icon "đã qua" trên hub marker

---

## 4. GET `/operator/missions/hubs`

**Mô tả:** Danh sách tất cả hub — load 1 lần khi vào trang để hiện hub marker trên map.

```json
[
  {
    "id": 1,
    "code": "HUB-01",
    "name": "Hub Quận 1",
    "latitude": 10.776900,
    "longitude": 106.700900,
    "status": "Active"
  }
]
```

---

## 5. Recommended flow khi mở Live Map

```
1. Load trang:
   GET /hubs → hiện tất cả hub marker (🏠)

2. setInterval mỗi 4s:
   GET /live-tracking → cập nhật vị trí drone marker (🚁)

3. Khi drone mới xuất hiện trong response:
   GET /{mission_id}/telemetry?limit=200 → vẽ polyline trail

4. Khi click vào drone marker:
   GET /{mission_id}/hub-checkpoints → hiện checkpoint log
   → zoom/fit bounds của polyline
```

---

## 6. Trạng thái drone trên map

| `status` | Xử lý |
|---|---|
| `Flying` | Hiện marker, cập nhật vị trí mỗi poll |
| Không có trong response | Drone đã landing — xóa marker khỏi map |

---

## 7. Vẽ trail kiểu Grab

```javascript
// Lấy lịch sử
const points = await fetchTelemetry(missionId, 200)
const coords = points.map(p => [p.latitude, p.longitude])

// Đường lịch sử (mờ)
L.polyline(coords, {
  color: 'rgba(129,140,248,0.35)',
  weight: 3,
}).addTo(map)

// Trail sáng — 20 điểm cuối
const tail = coords.slice(-20)
L.polyline(tail, {
  color: '#818cf8',
  weight: 5,
  className: 'trail-animated',
}).addTo(map)
```

```css
.trail-animated {
  stroke-dasharray: 8 6;
  animation: dash 1s linear infinite;
}
@keyframes dash {
  to { stroke-dashoffset: -14; }
}
```

---

## 8. Thư viện gợi ý

| Thư viện | Ghi chú |
|---|---|
| **React Leaflet** | Nhẹ, miễn phí, dễ tích hợp |
| **Mapbox GL JS** | Đẹp hơn, cần token (free tier 50k map loads/tháng) |

---

## 9. Sự cố (Incident) & Bay quay về (Return to Hub)

### 9.1 Kích hoạt Incident

**Điều kiện:**
- Xác suất **8%** xảy ra tại mỗi điểm telemetry (có thể điều chỉnh qua config)
- Không xảy ra ở 3 điểm đầu (tránh incident ngay khi cất cánh)

**Khi Incident xảy ra:**

1. **Mission Status thay đổi:**
   ```json
   {
     "mission_id": "MSN-20261018-0001",
     "status": "INCIDENT_RETURN",        // ← Từ FLYING → INCIDENT_RETURN
     "handling_status": "INCOMING",       // ← Drone đang bay về
     "incident_id": 3001
   }
   ```

2. **Incident Record được tạo:**
   ```json
   {
     "id": 3001,
     "mission_id": "MSN-20261018-0001",
     "drone_id": 28,
     "severity": "Medium",                // Low / Medium / High
     "description": "[SIMULATOR] Sự cố xảy ra tại (10.754890, 106.710782)...",
     "status": "OPEN",
     "reported_at": "2026-10-18T10:23:45",
     "requires_technical_inspection": true
   }
   ```

3. **MaintenanceAlert được tạo:**
   ```json
   {
     "id": 5001,
     "drone_id": "28",
     "source": "Incident",
     "incident_id": 3001,
     "title": "Sự cố chuyến bay MSN-20261018-0001 — Medium severity",
     "status": "Pending",
     "created_at": "2026-10-18T10:23:45"
   }
   ```

4. **Notification gửi tới Operators:**
   ```json
   {
     "user_id": "operator-123",
     "title": "⚠️ Sự cố Mission #MSN-20261018-0001",
     "message": "Drone #28 gặp sự cố tại (10.754890, 106.710782). Đang quay về Hub Quận 1. Severity: Medium.",
     "type": "SYSTEM_ALERT",
     "mission_id": "MSN-20261018-0001",
     "created_at": "2026-10-18T10:23:45"
   }
   ```

### 9.2 FE xử lý Notification

```javascript
// Lắng nghe thay đổi notification (WebSocket hoặc polling)
onNotificationReceived((notification) => {
  if (notification.type === 'SYSTEM_ALERT' && notification.message.includes('Sự cố')) {
    // 1. Hiện alert banner
    showAlert({
      type: 'danger',
      icon: '⚠️',
      title: notification.title,
      message: notification.message,
      action: {
        label: 'Xem chi tiết',
        onClick: () => navigateTo(`/missions/${notification.mission_id}`)
      }
    });
    
    // 2. Cập nhật mission status trong bảng
    updateMissionRow(notification.mission_id, {
      status: 'INCIDENT_RETURN',
      lastStatus: 'Gặp sự cố - Quay về hub'
    });
  }
});
```

### 9.3 Theo dõi Bay quay về (Return Flight)

**Mission Status từ INCIDENT_RETURN:**

```
GET /live-tracking  → Drone vẫn xuất hiện trong danh sách "Flying"
   ↓
GET /telemetry/{mission_id}  → Telemetry còn được ghi lại quá trình quay về
   ↓
Vẽ route quay về = Trail màu đỏ (khác với trail bình thường)
```

**Ví dụ vẽ Return Route:**

```javascript
// Khi nhận notification incident, fetch telemetry từ lúc incident tới landing
const telemetry = await fetch(
  `/api/v1/operator/missions/MSN-20261018-0001/telemetry?limit=1000`
).then(r => r.json());

// Tìm thời điểm incident (từ notification)
const incidentTime = new Date('2026-10-18T10:23:45');
const returnStartIndex = telemetry.findIndex(log => 
  new Date(log.timestamp) >= incidentTime
);

if (returnStartIndex !== -1) {
  const returnCoordinates = telemetry
    .slice(returnStartIndex)
    .map(point => [point.latitude, point.longitude]);
  
  // Vẽ polyline màu đỏ cho return route
  L.polyline(returnCoordinates, {
    color: '#ef4444',           // Màu đỏ
    weight: 4,
    dashArray: '5, 5',          // Đứt nét
    opacity: 0.8,
    className: 'return-route-animated'
  }).addTo(map).bindPopup('Return Route (Bay quay về)');
}
```

**CSS Animation cho Return Route:**

```css
.return-route-animated {
  stroke-dasharray: 8 6;
  animation: return-dash 1s linear infinite;
}

@keyframes return-dash {
  to { stroke-dashoffset: -14; }
}
```

### 9.4 Drone Landing tại Hub (Incident)

**Khi drone hạ cánh:**

```json
{
  "mission_id": "MSN-20261018-0001",
  "status": "INCIDENT_RETURN",
  "handling_status": "CANNOT_CONTINUE",     // ← Không thể tiếp tục
  "drone": {
    "id": 28,
    "current_hub_id": 1,                    // ← Đáp tại Hub Quận 1
    "battery_level_pct": 70
  }
}
```

**FE xử lý:**

```javascript
// Khi drone không còn trong live-tracking response (landing)
// 1. Xóa drone marker khỏi map
removeDroneMarker('MSN-20261018-0001');

// 2. Hiện notification
showAlert({
  type: 'info',
  title: 'Drone đã hạ cánh',
  message: 'Drone #28 đã đáp xuống Hub Quận 1 do sự cố. Cần kiểm tra kỹ thuật.',
  action: {
    label: 'Xem Maintenance Alert',
    onClick: () => navigateTo('/maintenance/alerts')
  }
});
```

---

## 10. Điều chỉnh Config Simulator (Testing)

**Endpoint:** `PATCH /api/v1/operator/simulator/config`

```bash
curl -X PATCH "http://localhost:8000/api/v1/operator/simulator/config" \
  -H "Authorization: Bearer <operator_token>" \
  -H "Content-Type: application/json" \
  -d {
    "incident_enabled": true,
    "incident_probability": 0.15,        # Tăng xác suất incident lên 15%
    "incident_min_step": 2,              # Bắt đầu từ bước 2
    "incident_severity": "High",         # Severity cao hơn
    "interval_sec": 2,                   # Giảm interval xuống 2s (bay nhanh)
    "steps_per_leg": 30                  # Tăng số điểm nội suy (bay mượt)
  }
```

**Response:**
```json
{
  "incident_enabled": true,
  "incident_probability": 0.15,
  "incident_min_step": 2,
  "incident_severity": "High",
  "interval_sec": 2,
  "steps_per_leg": 30
}
```

**Test Scenario:**
- Để `incident_probability = 1.0` → buộc incident xảy ra
- Để `incident_probability = 0.0` → không có incident
- Để `incident_severity = "High"` → hiện alert priority cao

---

## 11. Sắp có (chưa implement)

> Các endpoint dưới đây sẽ bổ sung sau khi backend hoàn thiện Task 2 & 3.

- **`/live-tracking` bổ sung thêm:**
  - `legs` — tuyến bay dự kiến (lat/lng từng chặng) để vẽ planned route
  - `current_leg` — đang bay chặng số mấy
  - `trail` — 50 điểm gần nhất inline

- **`GET /live-tracking/{mission_id}`** — chi tiết 1 drone khi FE click vào marker
