# 🆕 Technician - Tính Năng Mới Gần Đây

> **Base URL:** `https://isp492.onrender.com/api/v1/technician`

---

## 📍 1. Live Incoming Drones (Cập Nhật)

### GET `/incoming`

**Mới:**
- `current_leg` & `next_leg` → Hiển thị rõ leg đang bay & leg tiếp theo
- `next_leg.battery_id: null` → Technician biết cần chọn pin nào
- `handling_status` → AT_HUB, READY, INCOMING, CANNOT_CONTINUE

```json
{
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
  "handling_status": "AT_HUB"
}
```

---

## 🔋 2. Battery Swap - Tự Động Set Leg Tiếp Theo

### POST `/missions/{mission_id}/swap-battery`

**Mới:**
- Thay pin cũ → tự động set `next_leg.battery_id` (không cần chọn lại)
- Tự động lưu pin cũ vào `last_battery` nếu hỏng
- Tạo `MaintenanceAlert` nếu pin cũ bị damage

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
  "next_leg": {
    "battery_id": "BAT-002",
    "destination_hub": "Hub Quận 3"
  },
  "maintenance_alert_created": {
    "id": "MALERT-001",
    "title": "Pin BAT-001 hỏng phải thay thế"
  }
}
```

---

## ✈️ 3. Confirm Arrival - Lưu Pin Lúc Hạ Cánh

### POST `/missions/{mission_id}/confirm-arrival`

**Mới:**
- Lưu `battery_charge_at_arrival` → Phân tích tiêu hao pin chính xác
- Xem được lịch sử pin qua từng leg

```json
{
  "current_hub_id": 2,
  "handling_notes": "Drone hạ cánh an toàn, pin 87%"
}
```

**Response:**
```json
{
  "battery_charge_at_arrival": 87,
  "mission_history": [
    {
      "sequence_no": 1,
      "battery_start_pct": 95,
      "battery_end_pct": 87,
      "battery_charge_at_arrival": 87
    }
  ]
}
```

---

## 📊 4. Mission History - Tracking Pin Chi Tiết

### GET `/missions/{mission_id}/history`

**Mới:**
- Hiển thị `battery_charge_at_arrival` mỗi leg
- So sánh tiêu hao pin giữa các leg
- Phát hiện pin bị lỗi (tiêu hao quá nhanh)

```json
[
  {
    "sequence_no": 1,
    "leg_status": "COMPLETED",
    "battery_start_pct": 95,
    "battery_end_pct": 87,
    "battery_charge_at_arrival": 87,
    "distance_km": 8.5,
    "flight_time_min": 15.5
  }
]
```

---

## ⚠️ 5. Maintenance Alerts - Chi Tiết Đầy Đủ

### GET `/maintenance/alerts`

**Mới:**
- `description` field → Chi tiết vấn đề cụ thể
- `requires_technical_inspection` → Biết drone cần gửi kỹ thuật hay không
- Type rõ ràng: MAINTENANCE_DUE, BATTERY_DAMAGED, SIGNAL_LOSS, v.v.

```json
{
  "id": "MALERT-001",
  "drone_code": "DJI-M300-028",
  "title": "Pin BAT-003 hỏng phải thay thế",
  "description": "Pin BAT-003 sức khỏe 15% - phải thay thế ngay. Không sử dụng được nữa. Swap tại Hub Quận 7 vào 2026-10-18 14:30:00",
  "severity": "High",
  "type": "BATTERY_DAMAGED",
  "requires_technical_inspection": false,
  "status": "OPEN",
  "created_at": "2026-10-18T14:30:00"
}
```

---

## 🚁 6. Drone Status - 4 Trạng Thái Rõ Ràng

### PUT `/drones/{drone_id}/status`

**Các Status:**

| Status | Ý Nghĩa | Khi Nào |
|--------|---------|--------|
| **AVAILABLE** | Sẵn sàng, không bay, không bảo trì | Tại hub chờ dispatch |
| **IN_FLIGHT** | Đang bay | Chuyến bay đang thực hiện |
| **MAINTENANCE** | Bảo trì/sửa chữa | Kỹ thuật viên sửa |
| **RETIRED** | Ngừng hoạt động | Drone cũ không dùng |

```json
{
  "status": "MAINTENANCE",
  "reason": "Kiểm tra định kỳ hàng tháng"
}
```

---

## 🎯 Workflow Típico (Quick Flow)

```
1. GET /incoming
   ↓ (Drone bay tới hub)
   
2. POST /missions/{id}/confirm-arrival
   ↓ (Xác nhận hạ cánh, lưu pin 87%)
   
3. GET /hub/{hub_id}/batteries
   ↓ (Chọn pin mới)
   
4. POST /missions/{id}/swap-battery (BAT-001 → BAT-002)
   ↓ (Tự động set next_leg.battery_id = BAT-002)
   
5. POST /missions/{id}/mark-ready
   ↓ (Đánh dấu sẵn sàng)
   
6. POST /missions/{id}/dispatch
   ↓ (Dispatch drone)
   
7. Lặp lại từ bước 1
```

---

## 🔍 Compare: Trước vs Sau

| Tính Năng | Trước | Sau |
|-----------|-------|-----|
| **Relay Hub Info** | Không rõ leg hiện tại/tiếp theo | ✅ Hiển thị current_leg + next_leg |
| **Battery Swap** | Thay pin → cần chọn lại pin cho next_leg | ✅ Tự động set next_leg.battery_id |
| **Battery Tracking** | Không biết pin tiêu hao bao nhiêu | ✅ Lưu battery_charge_at_arrival mỗi leg |
| **Alert Details** | Chỉ có tiêu đề | ✅ Chi tiết đầy đủ + severity + reason |
| **Drone Status** | Không rõ trạng thái | ✅ AVAILABLE / IN_FLIGHT / MAINTENANCE / RETIRED |
| **Maintenance Inspection** | Không biết drone cần gửi kỹ thuật | ✅ Flag requires_technical_inspection |

---

## 📌 Tips for Frontend Integration

**1. Battery Swap Auto-Complete**
```javascript
// Sau khi swap, next_leg.battery_id đã set → không cần hỏi lại
const result = await swapBattery(missionId, oldBatId, newBatId)
console.log(result.next_leg.battery_id) // Already set!
```

**2. Alert Badge Severity**
```javascript
// Severity color
const alertColor = {
  'Low': 'blue',
  'Medium': 'orange',
  'High': 'red'
}
```

**3. Mission History Graph**
```javascript
// Vẽ line chart: leg vs battery_charge_at_arrival
// Phát hiện pin bị lỗi (dòng sụt quá nhanh)
```

---

**Last Updated:** 2026-10-18  
**Version:** Latest (after main merge)
