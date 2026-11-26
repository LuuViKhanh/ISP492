# 🔧 Technician - Thực Tế API (Corrected)

> **Cảnh báo:** File này sửa lại những sai sót trong TECHNICIAN_FEATURES.md và TECHNICIAN_WHATS_NEW.md
> 
> **Base URL:** `https://isp492.onrender.com/api/v1/technician`

---

## ✅ Các API Thực Tế Hiện Có

### 1️⃣ Incoming Missions (Chuyến Bay Đến)

#### GET `/incoming`

**Fields Thực Tế:**
```json
{
  "mission_code": "MSN-20261018-0001",
  "drone_id": 28,
  "drone_code": "DJI-M300-028",
  "model": "DJI M300 RTK",
  "mission_state": "FLYING",
  "battery_code": "TB-M300-001",
  "battery_percent": 87,
  "handling_status": "AT_HUB",
  "arrival_confirmed_at": "2026-10-18T14:23:45"
}
```

**⚠️ KHÔNG có:**
- ❌ `current_leg` (không trả leg info)
- ❌ `next_leg` (không trả leg info)
- ❌ `battery_id` field (chỉ có `battery_code`)

**Lấy leg info từ đâu?** → Cần gọi endpoint missions thêm để lấy leg info.

---

#### POST `/missions/{mission_id}/confirm-arrival`

**Request:**
```json
{
  "current_hub_id": 2,
  "handling_notes": "Drone hạ cánh an toàn, pin 87%"
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

**✅ Có:** `battery_charge_at_arrival` được lưu ✓

---

### 2️⃣ Chuyến Bay History

#### GET `/history`

**Fields Trả Về:**
```json
[
  {
    "mission_code": "MSN-20261018-0001",
    "arrived_at": "2026-10-18T13:15:30",
    "end_time": "2026-10-18T13:20:00",
    "status": "ARRIVAL_COMPLETED",
    "handling_status": null,
    "drone_code": "DJI-M300-028",
    "battery_code": "TB-M300-001",
    "battery_percent": 87,
    "destination_received_at": "2026-10-18T13:20:30"
  }
]
```

**⚠️ KHÔNG có:**
- ❌ `battery_start_pct` (chỉ có `battery_percent` = end)
- ❌ `distance_km`
- ❌ `flight_time_min`
- ❌ `sequence_no` / leg info

**Nhận Xét:** Chỉ hiển thị battery_percent cuối, không show toàn bộ hành trình leg.

---

### 3️⃣ Quản Lý Drone

#### GET `/drones`

**Fields:**
```json
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
```

**✅ Status Values:** AVAILABLE, IN_FLIGHT, MAINTENANCE, RETIRED ✓

---

#### GET `/drones/{drone_id}`

Trả toàn bộ profile drone

#### GET `/drones/{drone_id}/flight-history`

Lịch sử chuyến bay của drone

#### GET `/drones/{drone_id}/maintenance-history`

Lịch bảo trì của drone

#### PATCH `/drones/{drone_id}/status`

**Request:**
```json
{
  "status": "MAINTENANCE"
}
```

---

### 4️⃣ Quản Lý Pin

#### GET `/batteries`

**Fields:**
```json
[
  {
    "id": "BAT-001",
    "serial_number": "TB-M300-001",
    "capacity_wh": 4000,
    "status": "AVAILABLE",
    "charge_level_pct": 100,
    "battery_model": "DJI TB70",
    "last_charged_at": "2026-10-18T11:30:00",
    "lifecycle_status": "Active",
    "days_until_expiry": 180
  }
]
```

**Status Values:** AVAILABLE, IN_USE, CHARGING, REPLACED

#### POST `/batteries/{battery_id}/use`

**Request:**
```json
{
  "mission_id": "MSN-20261018-0001"
}
```

#### POST `/batteries/{battery_id}/mark-charged`

Đánh dấu pin đã sạc xong

---

### 5️⃣ Battery Swap - Thực Tế

#### POST `/missions/{mission_id}/swap-battery`

**Request:**
```json
{
  "current_battery_id": "BAT-001",
  "new_battery_id": "BAT-002",
  "damaged": true,
  "damage_notes": "Pin phồng, không sạc được"
}
```

**Response:**
```json
{
  "message": "Battery swapped successfully",
  "old_battery_id": "BAT-001",
  "new_battery_id": "BAT-002",
  "mission_id": "MSN-20261018-0001"
}
```

**⚠️ KHÔNG có:**
- ❌ Auto-set `next_leg.battery_id` (không trả leg info)
- ❌ `next_leg` info
- ❌ MaintenanceAlert tạo tự động

**Thực tế:** Chỉ thay pin, không auto-set leg tiếp theo

---

### 6️⃣ Maintenance Alerts

#### GET `/maintenance-alerts`

**Fields:**
```json
[
  {
    "id": 1,
    "drone_id": 28,
    "drone_code": "DJI-M300-028",
    "title": "Pin BAT-003 hỏng phải thay thế",
    "description": "Pin BAT-003 sức khỏe 15% - phải thay thế ngay",
    "severity": "High",
    "type": "BATTERY_DAMAGED",
    "status": "PENDING",
    "created_at": "2026-10-18T14:30:00",
    "requires_technical_inspection": false
  }
]
```

**Lưu Ý:** Chỉ show alert chưa xử lý (chưa có work_order_id)

#### GET `/maintenance-alerts/history`

Alert đã xử lý (đã có work_order_id)

#### GET `/maintenance-alerts/{alert_id}`

Chi tiết alert

#### PATCH `/maintenance-alerts/{alert_id}/acknowledge`

Xác nhận alert

**Request:**
```json
{
  "action_taken": "Đã sắp xếp bảo trì",
  "estimated_completion": "2026-10-19T10:00:00"
}
```

---

### 7️⃣ Maintenance Schedules

#### GET `/maintenance-schedules`

Danh sách lịch bảo trì

#### GET `/maintenance-schedules/drone/{drone_id}`

Lịch bảo trì của drone cụ thể

#### POST `/maintenance-schedules`

Tạo lịch bảo trì mới

---

### 8️⃣ Work Orders

#### GET `/work-orders`

Danh sách công việc bảo trì

#### GET `/work-orders/{work_order_id}`

Chi tiết công việc

#### POST `/work-orders`

Tạo công việc mới

#### PUT `/work-orders/{work_order_id}`

Cập nhật công việc

#### DELETE `/work-orders/{work_order_id}`

Xóa công việc

#### PATCH `/work-orders/{work_order_id}/inspection`

Cập nhật trạng thái kiểm tra

---

### 9️⃣ Relay Hub Flow (Multi-Leg Missions)

#### POST `/missions/{mission_id}/confirm-relay-arrival`

Xác nhận drone đáp relay hub trung gian (tương tự confirm-arrival nhưng cho relay)

#### POST `/missions/{mission_id}/swap-battery`

Thay pin tại relay hub

#### POST `/missions/{mission_id}/ready`

Đánh dấu drone sẵn sàng bay tiếp

#### POST `/missions/{mission_id}/dispatch`

Dispatch drone tới leg tiếp theo

---

### 🔟 Hub Inventory Management

#### GET `/hub-inventory`

Danh sách vật tư tại hub

#### GET `/hub-inventories`

Danh sách tồn kho

#### POST `/hub-inventories`

Tạo tồn kho

#### PATCH `/hub-inventories/{inventory_id}/adjust`

Điều chỉnh số lượng

---

## ⚠️ Sai Sót Trong Documentation Cũ

| Tính Năng Viết | Thực Tế | Status |
|---|---|---|
| `current_leg` & `next_leg` trong `/incoming` | ❌ Không có | **SAI** |
| Auto-set `next_leg.battery_id` sau swap | ❌ Không tự động | **SAI** |
| `battery_charge_at_arrival` mỗi leg | ✅ Có trong confirm-arrival & history | **ĐÚNG** |
| `GET /maintenance/alerts` | ✅ Là `/maintenance-alerts` | **SAI ENDPOINT** |
| MaintenanceAlert tự tạo khi swap | ❌ Không tự động tạo | **SAI** |
| Drone status: AVAILABLE/IN_FLIGHT/MAINTENANCE/RETIRED | ✅ Đúng | **ĐÚNG** |
| `/incoming` chỉ show relay hub AT_HUB/READY | ✅ Đúng | **ĐÚNG** |

---

## 📝 Kết Luận

**Những tính năng THỰC SỰ CÓ:**
✅ Confirm arrival + battery_charge_at_arrival  
✅ Drone status management  
✅ Battery swap  
✅ Maintenance alerts (chỉ đọc, không auto-create)  
✅ Work orders  
✅ Hub inventory  

**Những tính năng KHÔNG CÓ (chỉ là đề xuất):**
❌ current_leg / next_leg auto-populate  
❌ Auto-set battery cho next_leg  
❌ Auto-create MaintenanceAlert  
❌ Full mission history with leg details  

---

**Updated:** 2026-10-18  
**Note:** Dựa trên code thực tế từ `app/modules/fleet/technician/router.py`
