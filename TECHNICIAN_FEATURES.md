# 🔧 Technician Features - Hướng Dẫn Chức Năng

> **Base URL:** `https://isp492.onrender.com/api/v1`  
> **Auth:** `Authorization: Bearer <technician_token>` (Technician role)  
> **Prefix:** `/technician`

---

## 📋 Tổng Quan Chức Năng

Technician là vai trò chính tại các Hub, chịu trách nhiệm:
- ✈️ Quản lý chuyến bay đến/đi
- 🔋 Quản lý pin drone
- 🛠️ Bảo trì drone & công việc bảo trì
- 📦 Quản lý kho hàng & chi tiết thiết bị
- ⚠️ Theo dõi cảnh báo bảo trì

---

## 1️⃣ Quản Lý Chuyến Bay Đến (Incoming Drones)

### 1.1 GET `/technician/incoming`

**Mô tả:** Danh sách drone đang bay tới hub hiện tại. Chỉ show những relay hub đang ở trạng thái **AT_HUB** hoặc **READY** của leg hiện tại.

**Response:**
```json
{
  "count": 2,
  "incoming_missions": [
    {
      "mission_id": "MSN-20261018-0001",
      "drone_id": 28,
      "current_hub_id": 2,
      "from_hub": {
        "id": 1,
        "code": "HUB-01",
        "name": "Hub Quận 1",
        "latitude": 10.776900,
        "longitude": 106.700900
      },
      "to_hub": {
        "id": 3,
        "code": "HUB-03",
        "name": "Hub Quận 3",
        "latitude": 10.755890,
        "longitude": 106.707890
      },
      "current_leg": {
        "sequence_no": 1,
        "status": "IN_PROGRESS",
        "origin_hub_id": 1,
        "destination_hub_id": 2,
        "distance_km": 8.5
      },
      "next_leg": {
        "sequence_no": 2,
        "status": "PENDING",
        "origin_hub_id": 2,
        "destination_hub_id": 3,
        "distance_km": 12.3,
        "battery_id": null
      },
      "handling_status": "AT_HUB",
      "latest_latitude": 10.732500,
      "latest_longitude": 106.721800,
      "latest_altitude": 2.1,
      "latest_battery_pct": 87,
      "mission_status": "FLYING",
      "last_updated": "2026-10-18T14:23:45"
    }
  ]
}
```

**🆕 Tính Năng Mới:**
- `current_leg` & `next_leg` hiển thị rõ ràng → technician biết drone bay từ đâu đến đâu
- `next_leg.battery_id: null` → technician cần chọn pin trước khi dispatch tiếp
- `handling_status` ∈ [AT_HUB, READY] → drone sẵn sàng xử lý

---

### 1.2 POST `/technician/missions/{mission_id}/confirm-arrival`

**Mô tả:** Xác nhận drone đã hạ cánh tại hub relay. Chuyển trạng thái từ INCOMING → AT_HUB.

**Request:**
```json
{
  "current_hub_id": 2,
  "handling_notes": "Drone hạ cánh an toàn, pin 87%, không sự cố"
}
```

**Response:**
```json
{
  "mission_id": "MSN-20261018-0001",
  "handling_status": "AT_HUB",
  "current_hub_id": 2,
  "battery_remaining_pct": 87,
  "battery_charge_at_arrival": 87
}
```

**🆕 Tính Năng Mới:**
- `battery_charge_at_arrival` được lưu → dùng cho lịch sử bay & phân tích tiêu hao pin

---

### 1.3 GET `/technician/missions/{mission_id}/history`

**Mô tả:** Lịch sử chuyến bay (transit) của mission, bao gồm tất cả relay hub đã qua.

**Response:**
```json
[
  {
    "sequence_no": 1,
    "origin_hub": {
      "id": 1,
      "name": "Hub Quận 1"
    },
    "destination_hub": {
      "id": 2,
      "name": "Hub Quận 7"
    },
    "leg_status": "COMPLETED",
    "started_at": "2026-10-18T13:00:00",
    "completed_at": "2026-10-18T13:15:30",
    "battery_start_pct": 95,
    "battery_end_pct": 87,
    "battery_charge_at_arrival": 87,
    "distance_km": 8.5,
    "flight_time_min": 15.5
  },
  {
    "sequence_no": 2,
    "origin_hub": {
      "id": 2,
      "name": "Hub Quận 7"
    },
    "destination_hub": {
      "id": 3,
      "name": "Hub Quận 3"
    },
    "leg_status": "IN_PROGRESS",
    "started_at": "2026-10-18T13:30:00",
    "battery_start_pct": 87,
    "distance_km": 12.3
  }
]
```

---

## 2️⃣ Quản Lý Drone

### 2.1 GET `/technician/drones`

**Mô tả:** Danh sách tất cả drone tại hub hiện tại.

**Query Params:**
| Param | Type | Default | Mô tả |
|---|---|---|---|
| `status` | string | - | Filter: AVAILABLE, IN_FLIGHT, MAINTENANCE, RETIRED |
| `limit` | int | 50 | Số drone tối đa |
| `offset` | int | 0 | Bỏ qua N drone |

**Response:**
```json
{
  "total": 15,
  "drones": [
    {
      "id": 28,
      "code": "DJI-M300-028",
      "name": "DJI Matrice 300 RTK #028",
      "model": "DJI M300 RTK",
      "status": "AVAILABLE",
      "current_hub_id": 2,
      "battery_level_pct": 100,
      "last_maintenance": "2026-10-15T10:00:00",
      "maintenance_due_days": 5,
      "has_pending_alerts": false
    }
  ]
}
```

### 2.2 GET `/technician/drones/{drone_id}`

**Mô tả:** Chi tiết profile drone (specs, lịch sử bay, bảo trì).

**Response:**
```json
{
  "id": 28,
  "code": "DJI-M300-028",
  "name": "DJI Matrice 300 RTK #028",
  "model": "DJI M300 RTK",
  "manufacturer": "DJI",
  "status": "AVAILABLE",
  "max_flight_time_min": 55,
  "max_distance_km": 50,
  "payload_capacity_kg": 2.7,
  "cruise_speed_ms": 12,
  "cruise_altitude_m": 50,
  "current_hub_id": 2,
  "battery_level_pct": 100,
  "acquired_date": "2025-03-15",
  "total_flight_hours": 1240.5,
  "last_maintenance": "2026-10-15T10:00:00",
  "maintenance_due_days": 5
}
```

### 2.3 GET `/technician/drones/{drone_id}/flight-history`

**Mô tả:** Lịch sử chuyến bay của drone (tất cả các mission).

**Response:**
```json
[
  {
    "mission_id": "MSN-20261018-0001",
    "order_id": "ORD-20261018-001",
    "destination_hub": "Hub Quận 3",
    "distance_km": 8.5,
    "flight_time_min": 15,
    "battery_start_pct": 95,
    "battery_end_pct": 87,
    "status": "COMPLETED",
    "completed_at": "2026-10-18T13:15:30"
  }
]
```

### 2.4 PUT `/technician/drones/{drone_id}/status`

**Mô tả:** Cập nhật trạng thái droneZ

**Request:**
```json
{
  "status": "MAINTENANCE",
  "reason": "Kiểm tra định kỳ hàng tháng"
}
```

**Các Status Hợp Lệ:**
- `AVAILABLE`: Drone sẵn sàng, không bay, không bảo trì
- `IN_FLIGHT`: Drone đang bay
- `MAINTENANCE`: Drone đang bảo trì/sửa chữa
- `RETIRED`: Drone ngừng hoạt động

---

## 3️⃣ Quản Lý Pin (Battery Management)

### 3.1 GET `/technician/hub/{hub_id}/batteries`

**Mô tả:** Danh sách pin tại hub, bao gồm trạng thái sạc & tình trạng.

**Response:**
```json
{
  "available": 12,
  "charging": 5,
  "damaged": 1,
  "batteries": [
    {
      "id": "BAT-001",
      "code": "TB-M300-001",
      "model": "DJI TB70 Intelligent Flight Battery",
      "status": "AVAILABLE",
      "charge_level_pct": 100,
      "health_pct": 95,
      "cycle_count": 247,
      "last_charged": "2026-10-18T11:30:00"
    },
    {
      "id": "BAT-002",
      "code": "TB-M300-002",
      "status": "CHARGING",
      "charge_level_pct": 65,
      "estimated_ready_min": 8
    },
    {
      "id": "BAT-003",
      "code": "TB-M300-003",
      "status": "DAMAGED",
      "charge_level_pct": 0,
      "health_pct": 15
    }
  ]
}
```

### 3.2 POST `/technician/missions/{mission_id}/use-battery`

**Mô tả:** Gắn pin vào drone cho chuyến bay tiếp theo.

**Request:**
```json
{
  "battery_id": "BAT-001",
  "mission_id": "MSN-20261018-0001"
}
```

**Response:**
```json
{
  "message": "Battery assigned successfully",
  "battery_id": "BAT-001",
  "mission_id": "MSN-20261018-0001",
  "next_leg_sequence": 2
}
```

### 3.3 POST `/technician/batteries/{battery_id}/mark-charged`

**Mô tả:** Đánh dấu pin đã sạc xong.

**Response:**
```json
{
  "battery_id": "BAT-001",
  "status": "AVAILABLE",
  "charge_level_pct": 100,
  "health_pct": 95
}
```

### 3.4 POST `/technician/missions/{mission_id}/swap-battery`

**Mô tả:** Thay pin drone tại relay hub, chuẩn bị cho chặng bay tiếp theo.

**Request:**
```json
{
  "current_battery_id": "BAT-001",
  "new_battery_id": "BAT-002",
  "damaged": false,
  "damage_notes": ""
}
```

**Response:**
```json
{
  "message": "Battery swapped successfully",
  "old_battery_id": "BAT-001",
  "new_battery_id": "BAT-002",
  "mission_id": "MSN-20261018-0001",
  "next_leg": {
    "sequence_no": 2,
    "destination_hub": "Hub Quận 3",
    "distance_km": 12.3
  }
}
```

**🆕 Tính Năng Mới:**
- Tự động set `next_leg.battery_id` = pin mới → technician không cần chọn lại
- `mission.handling_status` chuyển từ READY → INCOMING (sẵn sàng dispatch)
- Nếu pin cũ bị hỏng → tạo `MaintenanceAlert` để kỹ thuật viên biết

---

## 4️⃣ Bảo Trì (Maintenance)

### 4.1 GET `/technician/maintenance/schedules`

**Mô tả:** Lịch bảo trì định kỳ của các drone.

**Response:**
```json
[
  {
    "id": "MSCHED-001",
    "drone_id": 28,
    "drone_code": "DJI-M300-028",
    "frequency_days": 30,
    "last_maintenance_date": "2026-10-15",
    "next_maintenance_date": "2026-11-14",
    "days_until_due": 5,
    "type": "Regular",
    "status": "DUE_SOON"
  }
]
```

### 4.2 POST `/technician/maintenance/records`

**Mô tả:** Tạo bản ghi bảo trì mới.

**Request:**
```json
{
  "drone_id": 28,
  "maintenance_type": "Inspection",
  "description": "Kiểm tra cam, battery, motor định kỳ tháng 10",
  "parts_replaced": ["Propeller Set", "ND Filter"],
  "work_hours": 2.5,
  "technician_notes": "Drone hoạt động bình thường, không lỗi"
}
```

**Response:**
```json
{
  "id": "MREC-001",
  "drone_id": 28,
  "maintenance_type": "Inspection",
  "completed_at": "2026-10-18T15:30:00",
  "status": "COMPLETED",
  "work_hours": 2.5
}
```

### 4.3 GET `/technician/maintenance-alerts`

**Mô tả:** Danh sách cảnh báo bảo trì chưa xử lý (tập trung, bảo trì sắp tới, pin hỏng). Technician chỉ thấy alert của drone tại hub mình.

**Endpoint Thêm:**
- `GET /technician/maintenance-alerts/history` — Alert đã xử lý (đã có work order)
- `GET /technician/maintenance-alerts/{alert_id}` — Chi tiết alert
- `PATCH /technician/maintenance-alerts/{alert_id}/acknowledge` — Xác nhận alert

**Response:**
```json
{
  "total": 3,
  "alerts": [
    {
      "id": "MALERT-001",
      "drone_id": 28,
      "drone_code": "DJI-M300-028",
      "title": "Định kỳ bảo trì sắp tới",
      "description": "Drone DJI-M300-028 cần bảo trì định kỳ trong 5 ngày nữa. Đã bay 1240 giờ.",
      "severity": "Medium",
      "type": "MAINTENANCE_DUE",
      "status": "OPEN",
      "created_at": "2026-10-15T10:00:00",
      "requires_technical_inspection": true
    },
    {
      "id": "MALERT-002",
      "drone_id": 28,
      "battery_id": "BAT-003",
      "title": "Pin hỏng phải thay thế",
      "description": "Pin BAT-003 sức khỏe 15% - phải thay thế ngay. Không sử dụng được nữa.",
      "severity": "High",
      "type": "BATTERY_DAMAGED",
      "status": "OPEN",
      "created_at": "2026-10-18T14:20:00",
      "requires_technical_inspection": false
    }
  ]
}
```

**🆕 Tính Năng Mới:**
- `description` field chi tiết → thay vì chỉ có tiêu đề
- Hiển thị lý do cụ thể (tập trung, pin sức khỏe, v.v.)
- `requires_technical_inspection` flag → biết drone cần gửi kỹ thuật hay không

### 4.4 POST `/technician/maintenance/alerts/{alert_id}/acknowledge`

**Mô tả:** Xác nhận đã nhận và xử lý cảnh báo.

**Request:**
```json
{
  "action_taken": "Đã sắp xếp bảo trì cho drone vào ngày mai",
  "estimated_completion": "2026-10-19T10:00:00"
}
```

---

## 5️⃣ Công Việc Bảo Trì (Work Orders)

### 5.1 GET `/technician/work-orders`

**Mô tả:** Danh sách công việc bảo trì cần thực hiện.

**Query Params:**
| Param | Type | Mô tả |
|---|---|---|
| `status` | string | PENDING, IN_PROGRESS, COMPLETED, CANCELLED |
| `priority` | string | LOW, MEDIUM, HIGH |

**Response:**
```json
{
  "total": 5,
  "work_orders": [
    {
      "id": "WO-001",
      "drone_id": 28,
      "drone_code": "DJI-M300-028",
      "title": "Kiểm tra định kỳ tháng 10",
      "description": "Kiểm tra camera, gimbal, motor, battery...",
      "priority": "HIGH",
      "status": "PENDING",
      "assigned_to": "Technician Name",
      "created_at": "2026-10-18T09:00:00",
      "due_date": "2026-10-20T17:00:00",
      "estimated_hours": 3
    }
  ]
}
```

### 5.2 POST `/technician/work-orders`

**Mô tả:** Tạo công việc bảo trì mới (technician hoặc admin).

**Request:**
```json
{
  "drone_id": 28,
  "title": "Kiểm tra định kỳ tháng 10",
  "description": "Kiểm tra camera, gimbal, motor, battery...",
  "priority": "HIGH",
  "estimated_hours": 3,
  "due_date": "2026-10-20T17:00:00"
}
```

### 5.3 PATCH `/technician/work-orders/{work_order_id}`

**Mô tả:** Cập nhật trạng thái công việc.

**Request:**
```json
{
  "status": "IN_PROGRESS",
  "progress_notes": "Đang kiểm tra camera và gimbal"
}
```

---

## 6️⃣ Quản Lý Kho Hàng (Inventory Management)

### 6.1 GET `/technician/hub/{hub_id}/inventory`

**Mô tả:** Danh sách chi tiết thiết bị tại hub (propellers, filters, batteries, v.v.).

**Response:**
```json
{
  "total_items": 150,
  "categories": [
    {
      "id": "CAT-001",
      "name": "Propellers",
      "items": [
        {
          "id": "INV-001",
          "part_code": "PROP-M300-SET",
          "part_name": "DJI Matrice 300 Propeller Set",
          "quantity": 25,
          "unit": "Set",
          "min_stock": 10,
          "status": "OK"
        }
      ]
    },
    {
      "id": "CAT-002",
      "name": "Filters",
      "items": [
        {
          "id": "INV-002",
          "part_code": "FILTER-ND8",
          "part_name": "ND8 Filter",
          "quantity": 8,
          "unit": "Piece",
          "min_stock": 5,
          "status": "LOW_STOCK"
        }
      ]
    }
  ]
}
```

### 6.2 POST `/technician/hub/{hub_id}/inventory/adjust`

**Mô tả:** Điều chỉnh số lượng hàng (sử dụng, nhập, v.v.).

**Request:**
```json
{
  "inventory_id": "INV-001",
  "quantity_change": -2,
  "reason": "Used for propeller replacement on DJI-M300-028"
}
```

**Response:**
```json
{
  "inventory_id": "INV-001",
  "previous_quantity": 25,
  "new_quantity": 23,
  "reason": "Used for propeller replacement on DJI-M300-028"
}
```

---

## 7️⃣ Dispatch & Mission Control

### 7.1 POST `/technician/missions/{mission_id}/mark-ready`

**Mô tả:** Đánh dấu mission sẵn sàng dispatch (sau khi thay pin, xác nhận drone).

**Request:**
```json
{
  "battery_id": "BAT-002",
  "drone_notes": "Drone sẵn sàng, pin 100%, thời tiết OK"
}
```

**Response:**
```json
{
  "mission_id": "MSN-20261018-0001",
  "handling_status": "READY",
  "message": "Mission ready for dispatch"
}
```

### 7.2 POST `/technician/missions/{mission_id}/dispatch`

**Mô tả:** Dispatch drone (khởi động chuyến bay).

**Request:**
```json
{
  "drone_id": 28,
  "battery_id": "BAT-002",
  "weather_conditions": "Clear sky, wind 5 km/h",
  "tech_notes": "All checks passed"
}
```

**Response:**
```json
{
  "mission_id": "MSN-20261018-0001",
  "status": "FLYING",
  "drone_id": 28,
  "dispatched_at": "2026-10-18T14:00:00",
  "estimated_arrival": "2026-10-18T14:12:30"
}
```

---

## 📊 Drone Status Reference

Drone có 4 trạng thái chính:

| Status | Mô Tả | Hành Động Có Thể |
|--------|-------|-----------------|
| **AVAILABLE** | Drone sẵn sàng tại hub, không bay, không bảo trì | Dispatch, Charging, Mark Maintenance |
| **IN_FLIGHT** | Drone đang bay, thi hành mission | Theo dõi, Failsafe RTH |
| **MAINTENANCE** | Drone đang bảo trì/sửa chữa, không sử dụng được | Check status, Complete maintenance |
| **RETIRED** | Drone ngừng hoạt động vĩnh viễn | Xem lịch sử |

### Status Transition (Chuyển Đổi Trạng Thái)

```
AVAILABLE 
   ↓ (dispatch)
IN_FLIGHT
   ↓ (landing)
AVAILABLE
   ↓ (mark maintenance)
MAINTENANCE
   ↓ (complete maintenance)
AVAILABLE
   ↓ (retire)
RETIRED
```

---

## 🔧 Workflow: Drone Status Change

**Ví dụ: Drone hạ cánh → Thay pin → Dispatch lại**

```
1. Drone IN_FLIGHT → Hạ cánh tại hub
   GET /technician/incoming
   
2. Technician confirm-arrival
   POST /technician/missions/{mission_id}/confirm-arrival
   → Mission.handling_status = AT_HUB
   
3. Thay pin drone
   POST /technician/missions/{mission_id}/swap-battery
   → Mission.next_leg.battery_id được set tự động
   
4. Mark sẵn sàng
   POST /technician/missions/{mission_id}/mark-ready
   → Mission.handling_status = READY
   
5. Dispatch drone
   POST /technician/missions/{mission_id}/dispatch
   → Drone.status = IN_FLIGHT (tự động)
   → Mission.status = FLYING
```

---

| Tính Năng | Mô Tả | Benefit |
|-----------|-------|---------|
| **battery_charge_at_arrival** | Lưu % pin khi drone hạ cánh | Phân tích tiêu hao pin chính xác |
| **current_leg / next_leg** | Hiển thị leg hiện tại + leg tiếp theo | Technician biết rõ drone bay từ đâu đến đâu |
| **handling_status** | AT_HUB, READY, INCOMING, CANNOT_CONTINUE | Biết rõ trạng thái xử lý drone |
| **MaintenanceAlert.description** | Chi tiết đầy đủ (incident, issue, severity) | Technician hiểu vấn đề cụ thể |
| **Relay Hub Filter** | Chỉ show relay hub khi leg IN_PROGRESS + AT_HUB/READY | Tránh hiển thị sai, gây nhầm lẫn |
| **Thay Pin Tự Động** | Khi swap pin, tự set battery_id cho next_leg | Giảm thao tác chọn lại pin |
| **Mission History** | Lịch sử đầy đủ với battery_charge_at_arrival | Theo dõi được thay đổi pin qua các leg |

---

## 📊 Workflow Típico (Typical Workflow)

```
1. Technician mở app → GET /incoming
   ↓
2. Drone bay tới hub → confirm-arrival (xác nhận)
   ↓
3. Technician xem pin → GET /hub/{hub_id}/batteries
   ↓
4. Thay pin cũ → swap-battery (battery swap tự động set next_leg.battery_id)
   ↓
5. Đánh dấu sẵn sàng → mark-ready
   ↓
6. Dispatch drone → dispatch (khởi động chuyến bay tiếp theo)
   ↓
7. Theo dõi → GET /incoming (lặp lại)
```

---

## 🔒 Quyền Truy Cập

| Action | Yêu Cầu |
|--------|---------|
| Xem incoming drones | Technician tại hub đó |
| Xác nhận/Dispatch | Technician tại hub đó |
| Quản lý bảo trì | Technician hoặc Admin |
| Thay pin | Technician tại hub đó |

---

## 🚀 Gợi Ý Tích Hợp Frontend

### Incoming Drones Widget
```javascript
useEffect(() => {
  const fetchIncoming = async () => {
    const res = await fetch('/api/v1/technician/incoming', { headers })
    const { incoming_missions } = await res.json()
    // Hiển thị drone sắp đến + current/next leg
    setIncomingDrones(incoming_missions)
  }
  
  const interval = setInterval(fetchIncoming, 10000) // Poll 10s
  return () => clearInterval(interval)
}, [])
```

### Battery Swap Flow
```javascript
const handleSwapBattery = async (missionId, oldBatId, newBatId) => {
  const res = await fetch(`/api/v1/technician/missions/${missionId}/swap-battery`, {
    method: 'POST',
    body: JSON.stringify({
      current_battery_id: oldBatId,
      new_battery_id: newBatId,
      damaged: false
    })
  })
  
  const { next_leg } = await res.json()
  // next_leg.battery_id đã được set tự động!
  showToast(`Pin thay xong! Sẵn sàng bay tới ${next_leg.destination_hub}`)
}
```

---

**Last Updated:** 2026-10-18  
**Version:** 2.0 (with speed_multiplier & battery tracking features)
