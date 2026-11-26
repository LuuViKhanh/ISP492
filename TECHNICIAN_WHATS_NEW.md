# 🆕 Technician - Tính Năng Mới

> **Base URL:** `https://isp492.onrender.com/api/v1/technician`

---

## ✨ 1. Current & Next Leg Info trong `/incoming`

**Commit:** `6c61d88`

### GET `/incoming` - Trả `current_leg` & `next_leg`

```json
{
  "mission_code": "MSN-20261018-0001",
  "drone_id": 28,
  "handling_status": "AT_HUB",
  "current_leg": {
    "id": 1,
    "sequence_no": 1,
    "from_hub_id": 1,
    "to_hub_id": 2,
    "status": "IN_PROGRESS"
  },
  "next_leg": {
    "id": 2,
    "sequence_no": 2,
    "from_hub_id": 2,
    "to_hub_id": 3,
    "battery_id": null,
    "status": "PENDING"
  }
}
```

**Lợi Ích:**
- Hiển thị rõ leg đang bay & leg tiếp theo
- Technician biết drone bay từ đâu đến đâu
- `next_leg.battery_id: null` → Biết cần chọn pin nào

---

## 🔋 2. Battery Charge at Arrival

**Commit:** `dcf3986`

### POST `/missions/{mission_id}/confirm-arrival` - Lưu pin lúc hạ cánh

```json
{
  "current_hub_id": 2,
  "handling_notes": "Drone hạ cánh"
}
```

**Response:**
```json
{
  "mission_status": "ARRIVAL_COMPLETED",
  "battery_charge_at_arrival": 87
}
```

### GET `/history` - Hiển thị `battery_charge_at_arrival`

```json
[
  {
    "mission_code": "MSN-20261018-0001",
    "battery_charge_at_arrival": 87,
    "arrived_at": "2026-10-18T13:15:30"
  }
]
```

**Lợi Ích:**
- Lưu pin lúc hạ cánh → Phân tích tiêu hao
- Phát hiện pin bị lỗi (tiêu hao quá nhanh)
- Báo cáo pin cho từng chuyến bay

---

**Last Updated:** 2026-10-18
