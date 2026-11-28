# History Endpoints Update - Images & Confirmation

**Branch:** `feat/proof-images-audit-trail`

## 📋 Tổng Quan

Thêm hình ảnh minh chứng vào các endpoint lịch sử chuyến bay và tạo endpoint mới để lịch sử xác nhận khách nhận hàng.

## 🔄 Endpoints Được Cập Nhật

### 1. GET `/api/v1/technician/fleet/outbound-history`

**Mô tả:** Lịch sử gửi hàng đi từ hub (khi khách gửi hàng + drone khởi hành)

**Trường mới thêm:**

| Field | Kiểu | Mô tả |
|-------|------|-------|
| `proof_images` | array | Hình ảnh minh chứng lúc nhận hàng + lúc bay đi (PACKAGE_RECEIVED_AT_ORIGIN_HUB, PACKAGE_LOADED_TO_DRONE) |
| `drone_image_url` | string | Ảnh status drone hiện tại (Base64 Data URL) |

**Ví dụ:**

```json
{
  "mission_id": "M001",
  "order_id": "ORD-2026-001",
  "customer_dropoff_at": "2026-10-18T09:00:00",
  "drone_departed_at": "2026-10-18T09:30:00",
  "receiver_name": "Nguyễn Văn A",
  "proof_images": [
    {
      "stage": "PACKAGE_RECEIVED_AT_ORIGIN_HUB",
      "proof_image_url": "data:image/png;base64,...",
      "checklist": [...],
      "recorded_at": "2026-10-18T09:00:00"
    },
    {
      "stage": "PACKAGE_LOADED_TO_DRONE",
      "proof_image_url": "data:image/png;base64,...",
      "checklist": [...],
      "recorded_at": "2026-10-18T09:25:00"
    }
  ],
  "drone_image_url": "data:image/png;base64,..."
}
```

**Tối ưu hóa:** Load all orders & drones cùng lúc (tránh N+1 query)

---

### 2. GET `/api/v1/technician/fleet/history`

**Mô tả:** Lịch sử chuyến bay đã hoàn thành (bay đi → bay tới → xác nhận)

**Trường mới thêm:**

| Field | Kiểu | Mô tả |
|-------|------|-------|
| `proof_images` | array | Hình ảnh lúc bay đi + bay tới (PACKAGE_LOADED_TO_DRONE, PACKAGE_UNLOADED_FROM_DRONE) |
| `drone_image_url` | string | Ảnh status drone hiện tại |
| `battery_image_url` | string | Ảnh status battery hiện tại (lúc thay pin) |

**Ví dụ:**

```json
{
  "mission_id": "M001",
  "order_id": "ORD-2026-001",
  "drone_departed_at": "2026-10-18T09:30:00",
  "drone_arrived_at": "2026-10-18T12:00:00",
  "destination_received_at": "2026-10-18T14:00:00",
  "proof_images": [
    {
      "stage": "PACKAGE_LOADED_TO_DRONE",
      "proof_image_url": "data:image/png;base64,...",
      "recorded_at": "2026-10-18T09:25:00"
    },
    {
      "stage": "PACKAGE_UNLOADED_FROM_DRONE",
      "proof_image_url": "data:image/png;base64,...",
      "recorded_at": "2026-10-18T12:05:00"
    }
  ],
  "drone_image_url": "data:image/png;base64,...",
  "battery_image_url": "data:image/png;base64,..."
}
```

**Tối ưu hóa:** Load all orders, drones, batteries, hubs cùng lúc (tránh N+1 query)

---

## ✨ Endpoint Mới

### 3. GET `/api/v1/technician/fleet/confirmation-history`

**Mô tả:** Lịch sử xác nhận khách hàng nhận hàng thành công tại hub

**Mục đích:** Theo dõi tất cả các lần khách hàng đã nhận hàng thành công, kèm hình ảnh minh chứng

**Trường trả về:**

| Field | Kiểu | Mô tả |
|-------|------|-------|
| `mission_id` | string | ID chuyến bay |
| `order_id` | string | ID đơn hàng |
| `origin_hub_id` | int | ID hub gốc |
| `origin_hub_name` | string | Tên hub gốc |
| `destination_hub_id` | int | ID hub đích |
| `destination_hub_name` | string | Tên hub đích |
| `drone_departed_at` | datetime | Lúc drone khởi hành |
| `drone_arrived_at` | datetime | Lúc drone đến hub |
| `customer_pickup_at` | datetime | ✅ Lúc khách nhận hàng |
| `receiver_name` | string | Tên khách hàng |
| `drone_id` | string | ID drone |
| `drone_code` | string | Mã drone |
| `battery_id` | string | ID pin |
| `battery_code` | string | Mã pin |
| `customer_pickup_proof` | object | 🖼️ Hình ảnh + checklist lúc khách nhận (CUSTOMER_PICKUP_CONFIRMED) |
| `drone_image_url` | string | 🖼️ Ảnh status drone hiện tại |
| `battery_image_url` | string | 🖼️ Ảnh status battery hiện tại |
| `status` | string | Status chuyến bay (always MISSION_COMPLETED) |

**Ví dụ Response:**

```json
[
  {
    "mission_id": "M001",
    "order_id": "ORD-2026-001",
    "origin_hub_id": 1,
    "origin_hub_name": "Hub Hà Nội",
    "destination_hub_id": 2,
    "destination_hub_name": "Hub Đà Nẵng",
    "drone_departed_at": "2026-10-18T09:30:00",
    "drone_arrived_at": "2026-10-18T12:00:00",
    "customer_pickup_at": "2026-10-18T14:30:00",
    "receiver_name": "Nguyễn Văn A",
    "drone_id": "DJI-M300-001",
    "drone_code": "M300-001",
    "battery_id": "BATTERY-001",
    "battery_code": "TB-001",
    "customer_pickup_proof": {
      "stage": "CUSTOMER_PICKUP_CONFIRMED",
      "proof_image_url": "data:image/png;base64,iVBORw0KGgo...",
      "checklist": [
        {
          "item": "Package condition",
          "checked": true,
          "note": "OK - No damage"
        },
        {
          "item": "Receiver signature",
          "checked": true,
          "note": "Signed by customer"
        }
      ],
      "recorded_at": "2026-10-18T14:30:00",
      "technician_id": "tech-001",
      "hub_id": 2
    },
    "drone_image_url": "data:image/png;base64,iVBORw0KGgo...",
    "battery_image_url": "data:image/png;base64,iVBORw0KGgo...",
    "status": "MISSION_COMPLETED"
  }
]
```

**Filter & Sorting:**
- Chỉ show missions có `status = MISSION_COMPLETED`
- Filter theo hub đích = hub của technician
- Sorted by `customer_pickup_at` descending (mới nhất trước)

**Tối ưu hóa:**
- Load all orders, drones, batteries cùng lúc
- Tránh N+1 query problem
- Extract hình ảnh CUSTOMER_PICKUP_CONFIRMED từ proof_images_json

---

## 📝 Chi Tiết Thay Đổi

### File Chính Được Sửa

**`app/modules/fleet/technician/router.py`**

#### 1. `get_outbound_history()` (Line ~3470)
- ✅ Thêm load orders, drones từ database cùng lúc
- ✅ Thêm `proof_images` và `drone_image_url` vào response

#### 2. `get_transit_history()` (Line ~312)
- ✅ Thêm load orders, drones, batteries, hubs từ database cùng lúc
- ✅ Thêm `proof_images`, `drone_image_url`, `battery_image_url` vào response

#### 3. `get_confirmation_history()` (Mới - Line ~3410)
- ✅ Endpoint hoàn toàn mới
- ✅ Query missions có status = MISSION_COMPLETED
- ✅ Filter theo destination_hub của technician
- ✅ Extract CUSTOMER_PICKUP_CONFIRMED image từ proof_images_json
- ✅ Load all orders, drones, batteries để tránh N+1 query
- ✅ Sort by customer_pickup_at descending

---

## 🎯 Use Cases

### Use Case 1: Xem Hành Trình Gửi Hàng
```
GET /api/v1/technician/fleet/outbound-history

Hiệu ứng: Xem khi khách gửi hàng + lúc drone khởi hành + hình ảnh khách gửi + hình ảnh drone chuẩn bị bay
```

### Use Case 2: Xem Hành Trình Nhận Tại Hub Đích
```
GET /api/v1/technician/fleet/history

Hiệu ứng: Xem lúc drone bay tới hub + hình ảnh lúc gỡ hàng + hình ảnh thay pin
```

### Use Case 3: Xem Lịch Sử Khách Nhận Hàng
```
GET /api/v1/technician/fleet/confirmation-history

Hiệu ứng: Xem tất cả những lần khách nhận hàng thành công + hình ảnh chứng minh + checklist
```

---

## 🔐 Quyền Hạn

- `allow_technician`: Technician chỉ xem lịch sử của hub mình
- `allow_admin`: Admin có thể xem lịch sử của bất kỳ hub nào (thông qua request context)

---

## 📊 Database Query Optimization

**Trước:** N+1 queries (1 query chính + N queries cho orders/drones)
```python
# ❌ Không tốt
for row in rows:
    order = await db.get(OrderModel, order_id)  # N queries
    drone = await db.get(Drone, drone_id)       # N queries
```

**Sau:** Batch queries (1 query chính + 1 query cho orders + 1 cho drones)
```python
# ✅ Tối ưu
order_map = {}
if order_ids:
    orders = await db.execute(select(OrderModel).where(OrderModel.id.in_(order_ids)))
    for order in orders.scalars():
        order_map[order.id] = order

drone_map = {}
if drone_ids:
    drones = await db.execute(select(Drone).where(Drone.id.in_(drone_ids)))
    for drone in drones.scalars():
        drone_map[drone.id] = drone

# Reuse maps
for row in rows:
    order = order_map.get(order_id)
    drone = drone_map.get(drone_id)
```

---

## 🚀 Commits

```
e77d0d8 - feat: auto-generate inspection checklist from defect type
c4bb3f6 - feat: add proof images and equipment images to outbound-history and history endpoints
878a9da - feat: add confirmation-history endpoint with customer pickup images
```

---

## ✅ Testing

Các endpoint có thể test bằng:

```bash
# 1. Outbound History
curl -X GET "http://localhost:8000/api/v1/technician/fleet/outbound-history" \
  -H "Authorization: Bearer <token>"

# 2. Transit History
curl -X GET "http://localhost:8000/api/v1/technician/fleet/history" \
  -H "Authorization: Bearer <token>"

# 3. Confirmation History (NEW)
curl -X GET "http://localhost:8000/api/v1/technician/fleet/confirmation-history" \
  -H "Authorization: Bearer <token>"
```

---

## 📌 Notes

- Tất cả hình ảnh được lưu dưới dạng **Base64 Data URL** (`data:image/png;base64,...`)
- Checklist items có cấu trúc: `{item: str, checked: bool, note: str, status: str}`
- Filter chỉ apply cho technician (admin không filter)
- Response sorted by datetime descending (mới nhất trước)
