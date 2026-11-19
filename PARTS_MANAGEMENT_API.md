# 📦 Parts Management API - Technician Guide

## Overview

Hệ thống quản lý vật tư cho Technician:
- **Xem tồn kho** vật tư tại Hub của bạn
- **Yêu cầu cấp phát** vật tư khi cần
- **Theo dõi trạng thái** yêu cầu
- **Nhận hàng** khi Admin phê duyệt

---

## 🔐 Authentication

Tất cả API endpoint yêu cầu header:
```
Authorization: Bearer <YOUR_JWT_TOKEN>
```

---

## 📊 Endpoints

### 1. Xem Tồn Kho Vật Tư Tại Hub

#### GET `/technician/fleet/hub-inventories`

Lấy danh sách tồn kho vật tư tại Hub của bạn.

**Query Parameters:**
```
- hub_id (optional): Nếu bạn muốn xem Hub khác (nếu là Admin)
```

**Response:**
```json
[
  {
    "id": 100,
    "hub_id": 1,
    "part_category_id": 1,
    "quantity": 20,
    "min_stock_threshold": 5,
    "updated_at": "2024-10-18T10:30:00Z"
  },
  {
    "id": 101,
    "hub_id": 1,
    "part_category_id": 2,
    "quantity": 2,  // 🚨 Dưới mức tối thiểu (min = 5)
    "min_stock_threshold": 5,
    "updated_at": "2024-10-18T10:30:00Z"
  }
]
```

**Field Giải Thích:**
- `id`: ID tồn kho duy nhất
- `hub_id`: Hub nào (1 = Quận 1, 2 = Quận 5,...)
- `part_category_id`: ID loại vật tư (liên kết với PartCategory)
- `quantity`: Số lượng hiện tại
- `min_stock_threshold`: Mức tối thiểu (nếu quantity < min → cần yêu cầu)
- `updated_at`: Lần cập nhật cuối cùng

---

### 2. Chi Tiết Tồn Kho (với thông tin loại vật tư)

#### GET `/technician/fleet/hub-inventories/{inventory_id}`

Lấy chi tiết một bản ghi tồn kho + thông tin loại vật tư.

**Path Parameters:**
```
inventory_id: ID tồn kho
```

**Response:**
```json
{
  "id": 100,
  "hub_id": 1,
  "part_category": {
    "id": 1,
    "name": "DJI Matrice 100 Propeller",
    "category_type": "Propeller",
    "compatible_drone_models": ["DJI Matrice 100", "Aurellia X6"],
    "unit_cost": 500000,
    "description": "Cánh quạt chính cho DJI Matrice 100",
    "is_active": true,
    "created_at": "2024-10-01T00:00:00Z"
  },
  "quantity": 20,
  "min_stock_threshold": 5,
  "is_low_stock": false,
  "updated_at": "2024-10-18T10:30:00Z"
}
```

---

### 3. Điều Chỉnh Số Lượng (Sử Dụng Vật Tư)

#### PATCH `/technician/fleet/hub-inventories/{inventory_id}/adjust`

Trừ hoặc cộng số lượng vật tư khi bạn sử dụng.

**Query Parameters:**
```
delta: Số lượng cần điều chỉnh (âm = trừ, dương = cộng)
```

**Example:**
```
PATCH /technician/fleet/hub-inventories/100/adjust?delta=-2

Ý nghĩa: Trừ 2 cái propeller (quantity: 20 → 18)
```

**Response:**
```json
{
  "id": 100,
  "hub_id": 1,
  "part_category_id": 1,
  "quantity": 18,
  "min_stock_threshold": 5,
  "updated_at": "2024-10-18T11:00:00Z"
}
```

**Lỗi Có Thể Xảy Ra:**
```json
// ❌ Không thể trừ làm âm
{
  "detail": "Số lượng điều chỉnh không hợp lệ: 2 + (-5) = -3 < 0"
}

// ❌ Tồn kho không tồn tại
{
  "detail": "HubInventory not found"
}
```

---

### 4. Yêu Cầu Cấp Phát Vật Tư

#### POST `/technician/fleet/parts-requests`

Gửi yêu cầu cấp phát vật tư khi tồn kho thiếu.

**Request Body:**
```json
{
  "part_category_id": 1,
  "quantity_needed": 5,
  "description": "Cánh quạt bị hỏng sau test bay, cần bổ sung",
  "drone_id": "drone-001",  // (Optional) Drone cần sửa
  "photo_urls": [           // (Optional) Ảnh chứng minh
    "https://example.com/photo1.jpg",
    "https://example.com/photo2.jpg"
  ]
}
```

**Response:**
```json
{
  "id": 500,
  "requested_by": "tech-001",
  "hub_id": 1,
  "drone_id": "drone-001",
  "part_category_id": 1,
  "quantity_needed": 5,
  "description": "Cánh quạt bị hỏng sau test bay, cần bổ sung",
  "photo_urls": ["https://example.com/photo1.jpg", "https://example.com/photo2.jpg"],
  "status": "Pending",
  "reviewed_by": null,
  "admin_note": null,
  "reviewed_at": null,
  "actual_cost": null,
  "created_at": "2024-10-18T11:30:00Z",
  "fulfilled_at": null
}
```

**Status Hiệu Lực:**
- `Pending` - Chờ Admin duyệt
- `Approved` - Admin duyệt, sắp mua hàng
- `Rejected` - Admin từ chối
- `Fulfilled` - Nhận hàng vào kho thành công

---

### 5. Xem Yêu Cầu Của Mình

#### GET `/technician/fleet/parts-requests/my`

Lấy danh sách tất cả yêu cầu cấp phát của bạn (Technician).

**Query Parameters:**
```
(Không có)
```

**Response:**
```json
[
  {
    "id": 500,
    "requested_by": "tech-001",
    "hub_id": 1,
    "drone_id": "drone-001",
    "part_category_id": 1,
    "quantity_needed": 5,
    "description": "Cánh quạt bị hỏng",
    "photo_urls": [...],
    "status": "Approved",  // 🟡 Chờ nhận hàng
    "reviewed_by": "admin-001",
    "admin_note": "Phê duyệt, mua hàng ngay",
    "reviewed_at": "2024-10-18T12:00:00Z",
    "actual_cost": 2500000,  // 500000 * 5 cái
    "created_at": "2024-10-18T11:30:00Z",
    "fulfilled_at": null
  },
  {
    "id": 501,
    "requested_by": "tech-001",
    "hub_id": 1,
    "drone_id": null,
    "part_category_id": 2,
    "quantity_needed": 3,
    "description": "Pin sắp hết",
    "photo_urls": [],
    "status": "Pending",  // ⏳ Chờ Admin duyệt
    "reviewed_by": null,
    "admin_note": null,
    "reviewed_at": null,
    "actual_cost": null,
    "created_at": "2024-10-18T13:00:00Z",
    "fulfilled_at": null
  }
]
```

---

### 6. Chi Tiết Một Yêu Cầu

#### GET `/technician/fleet/parts-requests/{request_id}`

Lấy chi tiết một yêu cầu cấp phát cụ thể.

**Path Parameters:**
```
request_id: ID yêu cầu (vd: 500)
```

**Response:**
```json
{
  "id": 500,
  "requested_by": "tech-001",
  "hub_id": 1,
  "drone_id": "drone-001",
  "part_category_id": 1,
  "quantity_needed": 5,
  "description": "Cánh quạt bị hỏng sau test bay, cần bổ sung",
  "photo_urls": ["https://example.com/photo1.jpg"],
  "status": "Approved",
  "reviewed_by": "admin-001",
  "admin_note": "Phê duyệt, mua hàng ngay",
  "reviewed_at": "2024-10-18T12:00:00Z",
  "actual_cost": 2500000,
  "created_at": "2024-10-18T11:30:00Z",
  "fulfilled_at": null
}
```

---

### 7. Nhận Hàng Vào Kho (Fulfill)

#### POST `/technician/fleet/parts-requests/{request_id}/fulfill`

Xác nhận nhận hàng → **HubInventory tự động cộng số lượng**.

**Path Parameters:**
```
request_id: ID yêu cầu
```

**Request Body:**
```json
{}  // Không cần tham số
```

**Response:**
```json
{
  "id": 500,
  "requested_by": "tech-001",
  "hub_id": 1,
  "drone_id": "drone-001",
  "part_category_id": 1,
  "quantity_needed": 5,
  "description": "Cánh quạt bị hỏng sau test bay, cần bổ sung",
  "photo_urls": ["https://example.com/photo1.jpg"],
  "status": "Fulfilled",  // ✅ Đã nhận hàng
  "reviewed_by": "admin-001",
  "admin_note": "Phê duyệt, mua hàng ngay",
  "reviewed_at": "2024-10-18T12:00:00Z",
  "actual_cost": 2500000,
  "created_at": "2024-10-18T11:30:00Z",
  "fulfilled_at": "2024-10-18T14:00:00Z"  // 📌 Lúc nhận hàng
}
```

**Tự động cập nhật HubInventory:**
```
Trước fulfill: quantity = 18
Sau fulfill:   quantity = 18 + 5 = 23 ✅
```

**Lỗi Có Thể Xảy Ra:**
```json
// ❌ Chỉ fulfill khi status = Approved
{
  "detail": "Chỉ có thể fulfill khi status = Approved, hiện tại: Pending"
}

// ❌ Yêu cầu không tồn tại
{
  "detail": "PartsRequest không tồn tại"
}

// ❌ Yêu cầu không phải của Hub bạn
{
  "detail": "PartsRequest không thuộc Hub của bạn"
}
```

---

## 🎯 Use Cases

### Use Case 1: Kiểm Tra Tồn Kho và Yêu Cầu Nếu Thiếu

```javascript
// 1. Lấy tồn kho
GET /technician/fleet/hub-inventories

// 2. Kiểm tra field `is_low_stock`
if (inventory.is_low_stock) {
  // 3. Yêu cầu cấp phát
  POST /technician/fleet/parts-requests
  {
    "part_category_id": inventory.part_category_id,
    "quantity_needed": 5,
    "description": "Tồn kho dưới mức tối thiểu"
  }
}

// 4. Hiển thị: "Đã gửi yêu cầu, chờ Admin duyệt"
```

---

### Use Case 2: Sử Dụng Vật Tư (Trừ Tồn Kho)

```javascript
// Technician tháo 2 cánh quạt để sửa drone
PATCH /technician/fleet/hub-inventories/100/adjust?delta=-2

// Response: quantity = 20 - 2 = 18
```

---

### Use Case 3: Nhận Hàng Mới

```javascript
// 1. Kiểm tra danh sách yêu cầu
GET /technician/fleet/parts-requests/my

// 2. Tìm yêu cầu có status = "Approved"
// 3. Khi hàng tới, xác nhận nhận hàng
POST /technician/fleet/parts-requests/500/fulfill

// 4. HubInventory tự động cập nhật
// 5. Status chuyển thành "Fulfilled"
```

---

## 📱 Frontend Flow Mẫu

```
┌─────────────────────────────────────────────┐
│ 1️⃣  DASHBOARD - Hiển thị Tồn Kho          │
├─────────────────────────────────────────────┤
│ GET /hub-inventories                        │
│ → Hiển thị danh sách vật tư                │
│ → Highlight nếu is_low_stock = true        │
└─────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────┐
│ 2️⃣  QUẢN LÝ TỒN KHO                       │
├─────────────────────────────────────────────┤
│ • Button "Sử Dụng" → PATCH /adjust?delta=-N│
│ • Button "Chi Tiết" → GET /{id}            │
│ • Button "Yêu Cầu" → POST /parts-requests  │
└─────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────┐
│ 3️⃣  YÊU CẦU CẤP PHÁT                      │
├─────────────────────────────────────────────┤
│ GET /parts-requests/my                      │
│ → Hiển thị danh sách yêu cầu               │
│ → Lọc theo status (Pending/Approved/...)   │
└─────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────┐
│ 4️⃣  NHẬN HÀNG                             │
├─────────────────────────────────────────────┤
│ Khi status = "Approved"                     │
│ → Button "Nhận Hàng" → POST /fulfill        │
│ → Tồn kho tự động cUp nhật                 │
└─────────────────────────────────────────────┘
```

---

## ⚠️ Chú Ý

1. **Chỉ xem tồn kho tại Hub của bạn**
   - Backend tự động filter theo `user.hub_id`
   - Không cần truyền `hub_id` (nếu truyền cũng bị bỏ qua)

2. **Chỉ nhận hàng khi status = Approved**
   - Nếu Admin chưa duyệt → không thể nhận
   - Khi nhận → status tự động chuyển thành `Fulfilled`

3. **HubInventory tự động cập nhật**
   - Khi fulfill → tồn kho tự động cộng
   - Không cần gọi API riêng để update tồn kho

4. **Giá thành tính từ unit_cost**
   - `actual_cost = unit_cost × quantity_needed`
   - Tính khi Admin phê duyệt (không phải khi yêu cầu)

5. **Upload ảnh**
   - Sử dụng `photo_urls` array
   - URL phải được upload trước (dùng upload API riêng)
   - FE chỉ cần truyền array of URLs

---

## 🔗 Related APIs

- **Admin Part Categories Management**: Xem `PARTS_MANAGEMENT_ADMIN_API.md`
- **Live Tracking**: Xem `LIVE_TRACKING_API.md`

---

## 💬 Support

Nếu có lỗi hoặc câu hỏi:
1. Kiểm tra Authorization header
2. Kiểm tra request body format
3. Xem error message từ API
4. Liên hệ Backend team
