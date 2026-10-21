# Technician Mission API — Hướng dẫn tích hợp FE

> Base URL: `https://isp492.onrender.com/api/v1`  
> Auth: `Authorization: Bearer <technician_token>`

---

## Tổng quan 2 trang mission

| Trang | Mô tả | API chính |
|---|---|---|
| **Outbound** | Đơn hàng chuẩn bị xuất phát từ hub | `GET /technician/deliveries/outbound` |
| **Transit (Incoming)** | Drone đang bay về hoặc đã đáp hub | `GET /technician/fleet/incoming` |

---

## TRANG 1 — OUTBOUND (Đơn xuất phát)

### Flow

```
KH thanh toán → Order.payment_status = PAID
    ↓
GET /outbound  (hiện đơn, chưa có drone/pin nếu chưa AT_HUB)
    ↓
Technician nhận hàng tại hub
POST /outbound/{orderId}/receive
    → Order.status = AT_HUB
    ↓
Operator tạo mission, chọn drone + pin
    → Mission.status = Scheduled, handling_status = At hub
    ↓
GET /outbound  (giờ hiện đầy đủ drone, pin, mission)
    ↓
Technician lắp pin (nếu cần đổi pin)
POST /outbound/{orderId}/prepare-battery  { battery_id?: string }
    ↓
Technician xác nhận sẵn sàng bay
POST /technician/fleet/missions/{mission_id}/ready
    → handling_status = Ready
    ↓
Technician bấm Dispatch
POST /technician/fleet/missions/{mission_id}/dispatch
    → Mission.status = Flying
    → Drone rời hub, Pin rời Battery Management
```

---

### `GET /technician/deliveries/outbound`

Trả danh sách đơn đang chờ xuất phát tại hub của Technician.

**Response mỗi item:**
```json
{
  "id": "ORD-20261018-0001",
  "order_id": "ORD-20261018-0001",
  "order_status": "AT_HUB",
  "origin_hub_id": 1,
  "package_label": "Hàng dễ vỡ",
  "payload_kg": 1.5,
  "destination_hub": "Hub Quận 7 - Phú Mỹ Hưng",
  "mission_id": "MSN-20261018-0001",
  "mission_status": "Scheduled",
  "handling_status": "At hub",
  "drone_id": "DRN-0005",
  "drone": "DJI FlyCart 15 Heavy - Unit H01-05",
  "drone_model": "DJI FlyCart 15 Heavy",
  "battery_id": "BAT-0009",
  "battery": "BAT-0009",
  "battery_model": "TB65 Battery"
}
```

**Lưu ý:**
- Khi `order_status != AT_HUB` → `mission_id`, `drone_id`, `battery_id` đều trả `null`
- Chỉ hiện đơn của hub hiện tại (lọc theo `user.hub_id`)

---

### `POST /technician/deliveries/outbound/{orderId}/receive`

Technician xác nhận đã nhận hàng từ khách tại hub.

**Điều kiện:** Order phải `PAID`, chưa `AT_HUB`  
**Idempotent:** Gọi lại sẽ không lỗi nếu đã `AT_HUB`

**Response:**
```json
{
  "message": "Package received at Hub",
  "order_id": "ORD-20261018-0001",
  "order_status": "AT_HUB",
  "origin_received_at": "2026-10-18T10:00:00"
}
```

---

### `POST /technician/deliveries/outbound/{orderId}/prepare-battery`

Lắp pin vào drone chuẩn bị bay. Nếu không truyền `battery_id`, backend tự chọn pin phù hợp.

**Body (optional):**
```json
{ "battery_id": "BAT-0009" }
```

---

### `POST /technician/fleet/missions/{mission_id}/ready`

Technician xác nhận drone đã kiểm tra xong, sẵn sàng bay.

**Điều kiện:** `handling_status = At hub`, phải có `drone_id` và `battery_id`

**Response:**
```json
{
  "message": "Mission đã sẵn sàng bay. Technician có thể bấm Dispatch.",
  "mission_id": "MSN-20261018-0001",
  "handling_status": "Ready",
  "drone_id": "DRN-0005",
  "battery_id": "BAT-0009"
}
```

---

### `POST /technician/fleet/missions/{mission_id}/dispatch`

Bấm Dispatch — drone cất cánh.

**Điều kiện:** `handling_status = Ready`

**Response:**
```json
{
  "message": "Drone đã được dispatch. Bay tiếp chặng 1 → Hub Quận 7",
  "mission_id": "MSN-20261018-0001",
  "status": "Flying",
  "handling_status": "Incoming",
  "dispatched_leg": {
    "id": 6,
    "sequence_no": 1,
    "from_hub_id": 1,
    "to_hub_id": 2,
    "status": "IN_PROGRESS",
    "started_at": "2026-10-18T10:05:00"
  },
  "remaining_legs": 0
}
```

---

## TRANG 2 — TRANSIT / INCOMING (Drone đang về hub)

### Flow

```
GET /technician/fleet/incoming  (poll mỗi 5-10s)
    ↓ hiện drone đang bay về hub hoặc đã đáp
    ↓
Khi mission_state = "Flying"  → drone đang trên đường, chờ
Khi mission_state = "Completed" → drone đã đáp hub đích, cần confirm
Khi mission_state = "Incident" → drone về do sự cố, cần xử lý

    ↓ [Nếu là relay hub - còn leg tiếp theo]
POST /missions/{id}/confirm-relay-arrival
    → handling_status = At hub, pin cũ tháo ra về Battery Management
POST /missions/{id}/swap-battery  { new_battery_id?: string }
    → lắp pin mới, handling_status = Ready
POST /missions/{id}/dispatch
    → drone bay tiếp chặng kế

    ↓ [Nếu là hub đích - chặng cuối]
POST /missions/{id}/confirm-arrival
    → Mission hoàn tất, drone về Hub Fleet, pin về Battery Management
```

---

### `GET /technician/fleet/incoming`

Poll mỗi **5-10 giây** để cập nhật danh sách.

**Response mỗi item:**
```json
{
  "mission_id": "MSN-20261014-0003",
  "mission_code": "MSN-20261014-0003",
  "order_id": "ORD-20261014-0001",
  "origin_hub_id": 1,
  "destination_hub_id": 2,
  "drone_id": "DRN-0005",
  "drone_code": "DJI FlyCart 15 Heavy - Unit H01-05",
  "model": "DJI FlyCart 15 Heavy",
  "mission_state": "Flying",
  "handling_status": "Incoming",
  "battery_id": "BAT-0009",
  "battery_code": "BAT-0009",
  "battery_percent": 87,
  "arrival_confirmed_at": null
}
```

**`mission_state` — 3 giá trị:**

| `mission_state` | Ý nghĩa | FE hiển thị |
|---|---|---|
| `Flying` | Drone đang bay về hub này | Badge xanh, chờ |
| `Completed` | Drone đã đáp hub đích, chờ confirm | Badge xanh lá, nút Confirm |
| `Incident` | Drone về do sự cố | Badge đỏ, nút Xử lý |

**Khi nào hiện trong /incoming:**
- Technician chỉ thấy drone đang bay **đến** hub mình (`MissionLeg IN_PROGRESS + to_hub_id = tech_hub`)
- Hoặc drone đang ở hub mình (`drone.current_hub_id = tech_hub`)
- Hoặc `destination_hub_id = tech_hub`

---

### `POST /technician/fleet/missions/{mission_id}/confirm-relay-arrival`

Technician xác nhận drone đã đáp relay hub — dùng khi còn leg tiếp theo.

**Kết quả:**
- `handling_status = At hub`
- Pin cũ: tháo khỏi drone, về Battery Management tại relay hub
- Drone: `current_hub_id = relay_hub`

---

### `POST /technician/fleet/missions/{mission_id}/swap-battery`

Thay pin tại relay hub. Nếu không truyền `new_battery_id`, backend tự chọn pin phù hợp.

**Body (optional):**
```json
{ "new_battery_id": "BAT-0012" }
```

**Kết quả:**
- Pin cũ: `drone_id = null`, ở lại hub sạc
- Pin mới: gắn vào drone
- `handling_status = Ready`

---

### `POST /technician/fleet/missions/{mission_id}/confirm-arrival`

Technician xác nhận drone đã đáp hub đích — kết thúc mission.

**Body (optional):**
```json
{
  "has_issue": false,
  "has_incident": false,
  "battery_faulty": false
}
```

**Kết quả:**
- `Mission.status = Completed`, rời khỏi `/incoming`
- Drone: về Hub Fleet tại hub đích
- Pin: về Battery Management tại hub đích
- Order: `status = DELIVERED_TO_HUB`, gửi email thông báo KH

---

## Tóm tắt nút bấm theo trạng thái

### Outbound

| `order_status` | `handling_status` | Nút hiển thị |
|---|---|---|
| `PENDING` / `AWAITING_DROP_OFF` | — | **Nhận hàng** → `/receive` |
| `AT_HUB` | `At hub` | **Sẵn sàng** → `/ready` |
| `AT_HUB` | `Ready` | **Dispatch** → `/dispatch` |

### Transit/Incoming

| `mission_state` | `handling_status` | Nút hiển thị |
|---|---|---|
| `Flying` | `Incoming` | Không có nút (đang bay) |
| `Flying` | `At hub` | **Confirm relay** → `/confirm-relay-arrival` |
| `Flying` | `Ready` | **Dispatch tiếp** → `/dispatch` |
| `Completed` | `Incoming` | **Confirm arrival** → `/confirm-arrival` |
| `Incident` | `Incoming` / `Cannot continue` | **Confirm arrival** → `/confirm-arrival` + tạo WO |
