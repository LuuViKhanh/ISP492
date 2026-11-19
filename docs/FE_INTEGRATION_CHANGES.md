# FE Integration — Tổng hợp API thay đổi

> **Base URL:** `https://droneoptai.id.vn/api/v1`  
> **Auth:** Bearer JWT (header `Authorization: Bearer <token>`)  
> **Ngày cập nhật:** 26/10/2026

---

## 0. Credentials Demo (password = `123` cho tất cả)

| Role | Email |
|---|---|
| Admin | `admin@drone.com` |
| Operator | `operator@droneoptai.com` |
| Customer | `test@test.com` |
| Tech Hub Bến Thành | `tech.benthanh@droneoptai.com` |
| Tech Hub Võ Thị Sáu | `tech.vothansu@droneoptai.com` |
| Tech Hub Tân Phong | `tech.tanphong@droneoptai.com` |
| Tech Hub Linh Chiểu | `tech.linhchieu@droneoptai.com` |
| Tech Hub Gò Vấp | `tech.nguyenoanh@droneoptai.com` |
| Tech Hub Bình Thạnh | `tech.ungvankhiem@droneoptai.com` |
| Tech Hub Tân Bình | `tech.conghoa@droneoptai.com` |
| Tech Hub An Lạc | `tech.anlac@droneoptai.com` |
| Tech Hub Nhà Bè | `tech.nhabe@droneoptai.com` |
| Tech Hub Hòa Thạnh | `tech.hoathanh@droneoptai.com` |

---

## 1. Notifications — Chuông thông báo 🔔

> **Áp dụng cho tất cả role.** Gọi sau khi login để hiển thị số badge trên chuông.

### 1.1 Lấy danh sách thông báo (có pagination)

```
GET /notifications?page=1&page_size=20&is_read=false&notification_type=Mission_Status
```

**Response:**
```json
{
  "total": 15,
  "unread_count": 4,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 42,
      "user_id": "...",
      "title": "✈️ Đơn #ORD-001 đã được lên lịch bay",
      "message": "Chuyến bay #MSN-001 đã được lên lịch...",
      "notifications_type": "Mission_Status",
      "reference_id": "MISSION_SCHEDULED_MSN-001",
      "is_read": false,
      "created_at": "2026-10-26T10:30:00Z",
      "mission_id": "MSN-001",
      "drone_id": null,
      "work_order_id": null
    }
  ]
}
```

**Query params:**

| Param | Kiểu | Mô tả |
|---|---|---|
| `page` | int | Trang (default: 1) |
| `page_size` | int | Số item/trang (max 100, default 20) |
| `is_read` | bool | `true`/`false` — lọc đã/chưa đọc |
| `notification_type` | string | `Mission_Status` / `Payment` / `System_Alert` / `AI_Warning` / `Maintenance` |

### 1.2 Số thông báo chưa đọc (badge chuông)

```
GET /notifications/unread-count
```
```json
{ "unread_count": 4 }
```

### 1.3 Đánh dấu đã đọc 1 thông báo

```
PATCH /notifications/{notification_id}/read
```

### 1.4 Đánh dấu đã đọc tất cả

```
PATCH /notifications/read-all
```

### 1.5 Xóa 1 thông báo *(MỚI)*

```
DELETE /notifications/{notification_id}
```

### 1.6 Xóa tất cả đã đọc *(MỚI)*

```
DELETE /notifications/clear-read
```
```json
{ "message": "Đã xóa 12 thông báo đã đọc." }
```

### 1.7 Bảng trigger thông báo theo role

| Sự kiện | Ai nhận | Title mẫu |
|---|---|---|
| Customer tạo đơn | Operator | 📦 Đơn hàng mới #ORD-xxx |
| Operator lên lịch mission | **Customer** *(MỚI)* | ✈️ Đơn #xxx đã được lên lịch bay |
| Drone tới Hub đích | Customer | 🚁 Drone đã tới Hub — Hàng của bạn đã về! |
| Đơn CRITICAL (quá hạn) | Operator + Customer | 🚨 CRITICAL — Đơn #xxx quá hạn |
| Đơn sắp hết hạn (MEDIUM/HIGH) | Operator + Technician | 🟡/🔴 [HIGH] Đơn sắp đến hạn |
| Work Order COMPLETED | **Admin + Operator** *(MỚI)* | ✅ Work Order #xxx hoàn tất |
| Tech tạo parts request (không đủ kho) | **Admin** *(MỚI)* | 🔧 Đề xuất linh kiện mới — Hub #x |
| Admin duyệt parts request | **Tech** *(MỚI)* | ✅ Đề xuất #xxx được duyệt |
| Admin từ chối parts request | **Tech** *(MỚI)* | ❌ Đề xuất #xxx bị từ chối |
| Mất tín hiệu drone > 3s | Operator | ⚠️ Cảnh báo mất tín hiệu Drone |
| Pickup reminder (5h/lần) | Customer | Kiện hàng đang chờ bạn tới nhận |

---

## 2. Supplies — Quản lý vật tư / linh kiện *(ĐỔI TÊN từ Inventory)*

> ⚠️ **Breaking Change**: Route `/inventory` → `/supplies`. Module cũ giữ lại backward-compat nhưng FE nên migrate.

### 2.1 Admin Supplies

**Tổng quan:**
```
GET /admin/supplies/summary
```
```json
{
  "total_items_in_stock": 284,
  "low_stock_alerts": 3,
  "pending_requests": 2,
  "total_categories": 12
}
```

**Xem kho toàn Hub:**
```
GET /admin/supplies?hub_id=1&low_stock_only=false&category_type=Motor
```

**Chi tiết kho 1 Hub *(MỚI)*:**
```
GET /admin/supplies/hubs/{hub_id}?low_stock_only=false
```
```json
{
  "hub_id": 1,
  "hub_name": "Hub Bến Thành",
  "total_sku": 8,
  "low_stock_count": 1,
  "estimated_stock_value": 12500000,
  "supplies": [
    {
      "id": 5,
      "part_name": "Cánh quạt DJI Mavic 3",
      "category_type": "Propeller",
      "quantity": 6,
      "min_stock_threshold": 2,
      "is_low_stock": false,
      "unit_cost": 350000
    }
  ]
}
```

**Nhập kho từ nhà cung cấp:**
```
POST /admin/supplies/receive
{
  "hub_id": 1,
  "part_category_id": 3,
  "quantity": 10,
  "note": "Nhập lô tháng 10"
}
```

**Nhập nhiều loại cùng lúc *(MỚI)*:**
```
POST /admin/supplies/hubs/{hub_id}/bulk-receive
{
  "hub_id": 1,
  "items": [
    { "part_category_id": 3, "quantity": 10 },
    { "part_category_id": 5, "quantity": 5, "unit_cost_override": 2000000 }
  ]
}
```

**Điều phối giữa Hub:**
```
POST /admin/supplies/transfer
{
  "from_hub_id": 1,
  "to_hub_id": 3,
  "part_category_id": 3,
  "quantity": 4,
  "note": "Bù hàng cho Hub Tân Phong"
}
```

**Điều chỉnh sau kiểm kê *(MỚI)*:**
```
POST /admin/supplies/hubs/{hub_id}/adjust
{
  "part_category_id": 3,
  "quantity_delta": -2,
  "reason": "Adjust",
  "note": "Kiểm kê thực tế thiếu 2 chiếc"
}
```

**Hủy linh kiện hỏng *(MỚI)*:**
```
POST /admin/supplies/hubs/{hub_id}/discard
{
  "part_category_id": 3,
  "quantity": 1,
  "note": "Cánh vỡ do va chạm"
}
```

**Cập nhật ngưỡng cảnh báo *(MỚI)*:**
```
PATCH /admin/supplies/hubs/{hub_id}/threshold
{
  "part_category_id": 3,
  "min_stock_threshold": 3
}
```

**Đề xuất linh kiện — xem tất cả + filter:**
```
GET /admin/supplies/parts-requests?status=Pending&hub_id=1&drone_id=DRN-0001
```

**Chi tiết 1 đề xuất *(MỚI)*:**
```
GET /admin/supplies/parts-requests/{request_id}
```

**Duyệt / Từ chối:**
```
PATCH /admin/supplies/parts-requests/{id}/approve
{ "admin_note": "Đã kiểm tra, cho phép sử dụng" }

PATCH /admin/supplies/parts-requests/{id}/reject
{ "admin_note": "Không phê duyệt — chưa đủ lý do" }
```

**Log biến động vật tư:**
```
GET /admin/supplies/transactions?hub_id=1&reason=RepairUse&limit=100
```
Reason values: `Receive` / `RepairUse` / `TransferIn` / `TransferOut` / `Adjust` / `Discard`

**Báo cáo tài chính:**
```
GET /admin/supplies/finance-report?month=10&year=2026
```

**Quản lý danh mục linh kiện:**
```
GET    /admin/supplies/categories
POST   /admin/supplies/categories
PATCH  /admin/supplies/categories/{id}
```

---

### 2.2 Technician Supplies

**Chi tiết kho tại Hub mình *(MỚI)*:**
```
GET /technician/supplies/hub-detail?low_stock_only=false&category_type=Motor
```

**Vật tư tại Hub:**
```
GET /technician/supplies
GET /technician/supplies/categories
```

**Tạo đề xuất linh kiện:**
```
POST /technician/supplies/parts-requests
{
  "drone_id": "DRN-0001",
  "part_category_id": 3,
  "quantity_needed": 2,
  "description": "Cánh bị nứt sau khi hạ cánh khẩn cấp",
  "photo_urls": ["https://..."]
}
```
> Nếu hub đủ hàng → `status: "AutoApproved"`, trừ kho ngay.  
> Nếu không đủ → `status: "Pending"`, chờ Admin duyệt → Admin nhận notification.

**Xem và xử lý đề xuất:**
```
GET   /technician/supplies/parts-requests?status=Pending
GET   /technician/supplies/parts-requests/{id}
PATCH /technician/supplies/parts-requests/{id}/fulfill?actual_cost=350000
```

**Kiểm kê thực tế *(MỚI)*:**
```
POST /technician/supplies/adjust
{
  "part_category_id": 3,
  "quantity_delta": -1,
  "note": "Đếm lại thực tế thiếu 1"
}
```

**Hủy linh kiện hỏng *(MỚI)*:**
```
POST /technician/supplies/discard
{
  "part_category_id": 3,
  "quantity": 1,
  "note": "Han gỉ, không dùng được"
}
```

**Đặt ngưỡng cảnh báo *(MỚI)*:**
```
PATCH /technician/supplies/threshold
{
  "part_category_id": 3,
  "min_stock_threshold": 4
}
```

**Lịch sử biến động tại Hub *(MỚI)*:**
```
GET /technician/supplies/transactions?part_category_id=3&limit=50
```

---

## 3. Battery Model Catalog *(MỚI)*

> Admin quản lý catalog model pin, FE hiển thị khi tạo/cập nhật pin.

### 3.1 Xem catalog

```
GET /admin/fleet/battery-models
```
```json
[
  {
    "id": 1,
    "name": "DJI TB30",
    "compatible_drone_models": ["DJI Mavic 3"],
    "capacity_wh": 77.0,
    "unit_cost": 2500000,
    "description": "Pin chính hãng cho DJI Mavic 3. ~46 phút bay."
  },
  {
    "id": 2,
    "name": "DJI TB60",
    "compatible_drone_models": ["DJI Matrice 300"],
    "capacity_wh": 273.1,
    "unit_cost": 5500000,
    "description": "Pin DJI TB60 cho Matrice 300 RTK."
  },
  {
    "id": 3,
    "name": "DJI TB65",
    "compatible_drone_models": ["DJI FlyCart 30"],
    "capacity_wh": 476.0,
    "unit_cost": 8500000,
    "description": "Pin DJI TB65 cho FlyCart 30 tải nặng."
  }
]
```

### 3.2 Tạo / cập nhật catalog

```
POST  /admin/fleet/battery-models
PATCH /admin/fleet/battery-models/{id}
```

### 3.3 API pin đã cập nhật

**GET list pin — thêm filter *(UPDATED)*:**
```
GET /admin/fleet/batteries?hub_id=1&battery_model=DJI+TB30&status=Active
```

**Response pin — thêm `unit_cost` + `compatible_drone_models` *(UPDATED)*:**
```json
{
  "id": "BAT-0001",
  "serial_number": "TB30-001",
  "battery_model": "DJI TB30",
  "capacity_wh": 77.0,
  "charge_level_pct": 100,
  "status": "Active",
  "available_status": "Ready",
  "lifecycle_status": "Active",
  "days_until_expiry": 680,
  "unit_cost": 2500000,
  "compatible_drone_models": ["DJI Mavic 3"],
  "current_hub_id": 1,
  "drone_id": null
}
```

**Thêm pin mới — thêm `battery_model` *(UPDATED)*:**
```
POST /admin/fleet/batteries
{
  "serial_number": "TB30-NEW-001",
  "battery_model": "DJI TB30",
  "current_hub_id": 1,
  "charge_level_pct": 100
}
```
> Nếu `capacity_wh` không truyền → tự điền từ catalog theo `battery_model`.

---

## 4. Logs — Activity & System *(MỚI)*

> Admin xem 2 loại log riêng biệt qua 2 endpoint mới.

### 4.1 Activity Logs (hành động user)

```
GET /admin/activity-logs?page=1&page_size=20&role=CUSTOMER&action=LOGIN&status=success
```

```json
{
  "total": 325,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 1,
      "user_id": "...",
      "user_email": "test@test.com",
      "role": "CUSTOMER",
      "action": "LOGIN",
      "resource_type": "users",
      "resource_id": "...",
      "description": "Đăng nhập thành công: test@test.com",
      "status": "success",
      "details_json": null,
      "created_at": "2026-10-26T10:00:00Z"
    }
  ]
}
```

**Filter params:** `user_id`, `action`, `role`, `resource_type`, `status`

**Action values:** `LOGIN` / `LOGOUT` / `REGISTER` / `PASSWORD_RESET` / `ARCHIVE_OLD_ACCOUNT` / `BATTERY_ADD` / `PARTS_REQUEST` / ...

### 4.2 System Logs (sự kiện cron/workers)

```
GET /admin/system-logs?page=1&page_size=50&source=run_critical_order_notifier&level=ERROR
```

```json
{
  "total": 48,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "id": 1,
      "source": "run_critical_order_notifier",
      "level": "INFO",
      "event": "WORKER_RUN",
      "message": "Worker 'run_critical_order_notifier' chạy thành công.",
      "details_json": null,
      "created_at": "2026-10-26T11:00:00Z"
    }
  ]
}
```

**Level values:** `INFO` / `WARNING` / `ERROR` / `CRITICAL`  
**Event values:** `WORKER_RUN` / `WORKER_ERROR` / `WORKER_FAILED`  
**Source values:** tên worker (`run_critical_order_notifier`, `run_deadline_alert_watcher`, ...)

---

## 5. Dashboard Admin — Finance Report *(đã cập nhật)*

```
GET /admin/supplies/finance-report?month=10&year=2026
```

```json
{
  "period": "Tháng 10/2026",
  "revenue": {
    "delivery_fees": 4250000,
    "total_income": 4250000
  },
  "expense": {
    "parts_procurement": 1750000,
    "parts_repair_use": 350000,
    "total_expense": 2100000,
    "by_category": {
      "Cánh quạt DJI Mavic 3": 1050000,
      "Cáp nguồn pin": 700000
    }
  },
  "net_cashflow": 2150000,
  "summary": {
    "total_parts_requests": 8,
    "fulfilled_requests": 5,
    "profit_margin_pct": 50.6
  }
}
```

---

## 6. Hubs — Địa chỉ đã cập nhật theo phường mới

| Hub ID | Code | Tên mới | Địa chỉ |
|---|---|---|---|
| 1 | HUB-BT1 | Hub Bến Thành | 1 Lê Lợi, Phường Bến Thành, TP.HCM |
| 2 | HUB-VTS | Hub Võ Văn Tần | 125 Võ Văn Tần, Phường Võ Thị Sáu, TP.HCM |
| 3 | HUB-TP | Hub Tân Phong | 10 Nguyễn Văn Linh, Phường Tân Phong, TP.HCM |
| 4 | HUB-LC | Hub Linh Chiểu | 371 Võ Văn Ngân, Phường Linh Chiểu, TP.HCM |
| 5 | HUB-NO | Hub Nguyễn Oanh | 200 Nguyễn Oanh, Phường 17, Quận Gò Vấp, TP.HCM |
| 6 | HUB-UVK | Hub Ung Văn Khiêm | 100 Ung Văn Khiêm, Phường 25, Quận Bình Thạnh, TP.HCM |
| 7 | HUB-CH | Hub Cộng Hòa | 15 Cộng Hòa, Phường 4, Quận Tân Bình, TP.HCM |
| 8 | HUB-AL | Hub An Lạc | 450 Kinh Dương Vương, Phường An Lạc, Quận Bình Tân, TP.HCM |
| 9 | HUB-NB | Hub Nhà Bè | 1000 Huỳnh Tấn Phát, Phường Nhà Bè, TP.HCM |
| 10 | HUB-HT | Hub Hòa Thạnh | 300 Lũy Bán Bích, Phường Hòa Thạnh, Quận Tân Phú, TP.HCM |

---

## 7. Breaking Changes & Migration checklist

### ⚠️ Route đổi tên (phải cập nhật FE)

| Cũ | Mới | Ghi chú |
|---|---|---|
| `GET /admin/inventory` | `GET /admin/supplies` | Cũ vẫn chạy |
| `GET /admin/inventory/categories` | `GET /admin/supplies/categories` | |
| `POST /admin/inventory/receive` | `POST /admin/supplies/receive` | |
| `GET /admin/inventory/transactions` | `GET /admin/supplies/transactions` | |
| `GET /admin/inventory/parts-requests` | `GET /admin/supplies/parts-requests` | |
| `GET /technician/inventory` | `GET /technician/supplies` | |
| `POST /technician/inventory/parts-requests` | `POST /technician/supplies/parts-requests` | |

### ⚠️ Response field thêm mới (backward-compat, không break)

- `GET /notifications` → trả `NotificationListResponse` thay vì `list[...]` trực tiếp (có `total`, `unread_count`, `items`)
- `GET /admin/fleet/batteries` → thêm `unit_cost`, `compatible_drone_models` trong mỗi item
- `POST /admin/fleet/batteries` → `capacity_wh` giờ là optional nếu truyền `battery_model`

### DB tables đổi tên (không ảnh hưởng FE, chỉ ảnh hưởng nếu FE query thẳng SQL)

| Cũ | Mới |
|---|---|
| `hub_inventories` | `hub_supplies` |
| `inventory_transactions` | `supply_transactions` |

---

## 8. PartsRequest Status Flow

```
Tech tạo → PENDING (chờ Admin) hoặc AUTO_APPROVED (đủ kho, trừ ngay)
         ↓
Admin duyệt → APPROVED (trừ kho, Tech nhận notification)
Admin từ chối → REJECTED (Tech nhận notification)
Không đủ kho → WAITING_STOCK (Admin cần transfer)
         ↓
Tech xác nhận xong → FULFILLED (+ nhập actual_cost)
```

---

*Tài liệu này được tạo tự động từ session dev ngày 26/10/2026.*  
*Cần thêm thông tin: liên hệ Backend team hoặc xem Swagger tại `https://droneoptai.id.vn/api/v1/openapi.json`*
