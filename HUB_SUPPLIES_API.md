# Hub Supplies Management — Hướng dẫn API cho Frontend

> Base URL: `https://isp492.onrender.com/api/v1`  
> Auth: `Authorization: Bearer <technician_token>` (Technician hoặc Admin role)

---

## Tổng quan

**Hub Supplies** là hệ thống quản lý vật tư tiêu hao tại các trạm hub, bao gồm:
- 🔩 **Cánh quạt** (propellers)
- 🛠️ **Bộ sửa chữa** (repair kits)
- 🔋 **Tấm pin dự phòng** (backup batteries)
- 🧰 **Vật tư khác** (other consumables)

**Mục đích:**
- Theo dõi số lượng tồn kho tại mỗi hub
- Cảnh báo khi hàng tồn kho thấp
- Hỗ trợ quản lý logistics

---

## 1. GET `/technician/supplies` — Lấy danh sách vật tư

**Mô tả:** Lấy danh sách vật tư tại hub của technician. Nếu là Admin, có thể lọc theo hub cụ thể.

**Query Parameters:**
| Param | Type | Bắt buộc | Mô tả |
|---|---|---|---|
| `hub_id` | int | Không | (Admin only) Lọc theo hub_id. Nếu không truyền, trả tất cả |

**Request:**
```bash
# Technician — lấy vật tư tại hub của mình
GET /technician/supplies

# Admin — lọc theo hub
GET /technician/supplies?hub_id=2
```

**Response (200):**
```json
[
  {
    "id": 1,
    "hub_id": 2,
    "item_name": "Propeller DJI Mini 4 Pro",
    "quantity": 15,
    "unit": "chiếc",
    "created_at": "2026-10-18T08:30:00",
    "updated_at": "2026-10-18T14:22:15"
  },
  {
    "id": 2,
    "hub_id": 2,
    "item_name": "Repair Kit (Motor)",
    "quantity": 5,
    "unit": "bộ",
    "created_at": "2026-10-18T08:30:00",
    "updated_at": "2026-10-18T10:45:30"
  },
  {
    "id": 3,
    "hub_id": 2,
    "item_name": "Backup Battery (TB60)",
    "quantity": 8,
    "unit": "cái",
    "created_at": "2026-10-18T08:30:00",
    "updated_at": "2026-10-18T12:00:00"
  }
]
```

**Ghi chú:**
- Technician chỉ xem vật tư tại hub của mình (dù truyền `hub_id` cũng bị bỏ qua)
- Danh sách được sắp xếp theo ID (cũ nhất trước)
- `created_at/updated_at` dùng để biết khi nào thêm/sửa vật tư

---

## 2. POST `/technician/supplies` — Thêm vật tư mới

**Mô tả:** Tạo bản ghi vật tư mới tại hub.

**Request Body:**
```json
{
  "hub_id": 2,                          // (Admin only) — Technician tự động lấy từ hub_id của user
  "item_name": "Propeller DJI Mini 4",
  "quantity": 20,
  "unit": "chiếc"
}
```

**Response (201):**
```json
{
  "id": 4,
  "hub_id": 2,
  "item_name": "Propeller DJI Mini 4",
  "quantity": 20,
  "unit": "chiếc",
  "created_at": "2026-10-18T15:30:00",
  "updated_at": "2026-10-18T15:30:00"
}
```

**Lỗi có thể gặp:**

| Status | Error | Mô tả |
|---|---|---|
| 400 | `Technician chưa được gán Hub nào.` | Technician chưa có hub_id trong profile |
| 400 | `hub_id là bắt buộc khi Admin tạo vật tư.` | Admin không truyền hub_id |
| 404 | `Hub #{id} không tồn tại.` | Hub không tồn tại trong DB |

**Ví dụ FE:**
```javascript
async function addSupply() {
  const response = await fetch('/api/v1/technician/supplies', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      item_name: 'Propeller DJI Mini 4',
      quantity: 20,
      unit: 'chiếc'
    })
  });

  if (response.ok) {
    const supply = await response.json();
    console.log('Vật tư mới:', supply);
    // Cập nhật bảng UI
  }
}
```

---

## 3. PUT `/technician/supplies/{supply_id}` — Cập nhật vật tư

**Mô tả:** Cập nhật thông tin vật tư (tên, số lượng, đơn vị).

**Path Parameters:**
| Param | Type | Mô tả |
|---|---|---|
| `supply_id` | int | ID của vật tư |

**Request Body (Partial Update):**
```json
{
  "item_name": "Propeller DJI Mini 4 Pro (Updated)",
  "quantity": 25,
  "unit": "chiếc"
}
```

Bạn có thể truyền chỉ một số field (không cần truyền tất cả):
```json
{
  "quantity": 18
}
```

**Response (200):**
```json
{
  "id": 1,
  "hub_id": 2,
  "item_name": "Propeller DJI Mini 4 Pro (Updated)",
  "quantity": 25,
  "unit": "chiếc",
  "created_at": "2026-10-18T08:30:00",
  "updated_at": "2026-10-18T15:45:30"
}
```

**Lỗi có thể gặp:**

| Status | Error | Mô tả |
|---|---|---|
| 404 | `Supply not found` | Vật tư không tồn tại |
| 403 | `Vật tư không thuộc Hub của bạn.` | Technician cố sửa vật tư tại hub khác |

---

## 4. PATCH `/technician/supplies/{supply_id}/adjust` — Điều chỉnh số lượng

**Mô tả:** Tăng hoặc giảm số lượng vật tư dựa trên delta (có thể âm).

**Query Parameters:**
| Param | Type | Bắt buộc | Mô tả |
|---|---|---|---|
| `delta` | int | Có | Số lượng cộng thêm (âm để trừ) |

**Request:**
```bash
# Thêm 5 chiếc propeller
PATCH /technician/supplies/1/adjust?delta=5

# Trừ 2 chiếc propeller (sử dụng)
PATCH /technician/supplies/1/adjust?delta=-2
```

**Response (200):**
```json
{
  "id": 1,
  "hub_id": 2,
  "item_name": "Propeller DJI Mini 4 Pro",
  "quantity": 18,
  "unit": "chiếc",
  "created_at": "2026-10-18T08:30:00",
  "updated_at": "2026-10-18T16:00:15"
}
```

**Lỗi có thể gặp:**

| Status | Error | Mô tả |
|---|---|---|
| 400 | `Số lượng điều chỉnh không hợp lệ: ... < 0` | Sau điều chỉnh, số lượng âm |
| 404 | `Supply not found` | Vật tư không tồn tại |
| 403 | `Vật tư không thuộc Hub của bạn.` | Technician cố điều chỉnh vật tư tại hub khác |

**Ví dụ FE (sử dụng propeller sau khi sửa drone):**
```javascript
async function useSupply(supplyId, quantity) {
  const response = await fetch(
    `/api/v1/technician/supplies/${supplyId}/adjust?delta=-${quantity}`,
    {
      method: 'PATCH',
      headers: {
        'Authorization': `Bearer ${token}`,
      }
    }
  );

  if (response.ok) {
    const updated = await response.json();
    console.log(`Số lượng mới: ${updated.quantity}`);
    showNotification(`Đã sử dụng ${quantity} chiếc, còn ${updated.quantity} chiếc`);
  } else {
    showError('Số lượng không đủ hoặc lỗi khác');
  }
}
```

---

## 5. DELETE `/technician/supplies/{supply_id}` — Xóa vật tư

**Mô tả:** Xóa bản ghi vật tư (dùng khi vật tư không còn sử dụng).

**Request:**
```bash
DELETE /technician/supplies/1
```

**Response (204):**
```
[Không có response body]
```

**Lỗi có thể gặp:**

| Status | Error | Mô tả |
|---|---|---|
| 404 | `Supply not found` | Vật tư không tồn tại |
| 403 | `Vật tư không thuộc Hub của bạn.` | Technician cố xóa vật tư tại hub khác |

---

## 6. Trường hợp sử dụng — Workflow thực tế

### 6.1 Technician nhận ca làm việc

1. **Vào trang Hub** → Xem danh sách vật tư:
   ```javascript
   const supplies = await fetch('/api/v1/technician/supplies', {
     headers: { 'Authorization': `Bearer ${token}` }
   }).then(r => r.json());
   
   // Render bảng vật tư
   renderSuppliesTable(supplies);
   ```

### 6.2 Sửa chữa drone — Sử dụng vật tư

2. **Kỹ thuật viên tháo propeller hỏng** → Trừ 1 chiếc propeller:
   ```javascript
   await fetch('/api/v1/technician/supplies/1/adjust?delta=-1', {
     method: 'PATCH',
     headers: { 'Authorization': `Bearer ${token}` }
   });
   // UI cập nhật: 15 → 14 chiếc
   ```

### 6.3 Nhận hàng mới — Thêm vật tư

3. **Kỹ thuật viên nhận shipment** → Thêm vật tư mới:
   ```javascript
   await fetch('/api/v1/technician/supplies', {
     method: 'POST',
     headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
     body: JSON.stringify({
       item_name: 'Propeller DJI Mini 4',
       quantity: 20,
       unit: 'chiếc'
     })
   });
   // UI refresh danh sách
   ```

### 6.4 Cảnh báo tồn kho thấp

4. **FE kiểm tra tồn kho** → Alert nếu dưới ngưỡng:
   ```javascript
   const supplies = await fetchSupplies();
   const lowStockThreshold = 5;
   
   supplies.forEach(supply => {
     if (supply.quantity < lowStockThreshold) {
       showAlert({
        type: 'warning',
        message: `⚠️ ${supply.item_name} chỉ còn ${supply.quantity} ${supply.unit}`
       });
     }
   });
   ```

---

## 7. Best Practices

### ✅ Làm

- **Tính toán ngưỡng cảnh báo** — Đặt low stock threshold theo loại vật tư (propeller: 5, repair kit: 2, v.v.)
- **Log từng thao tác** — Ghi nhận ai, khi nào, tăng/giảm bao nhiêu (cho audit trail)
- **Hiện history** — Nên thêm endpoint để xem lịch sử thay đổi vật tư (tùy chọn Backend)
- **Refresh tự động** — Poll `/supplies` mỗi 30–60s để cập nhật realtime

### ❌ Không làm

- **Không truyền hub_id nếu là Technician** — Backend sẽ bỏ qua
- **Không cho phép số lượng âm** — Backend sẽ reject
- **Không xóa vật tư đang dùng** — Archive hoặc đặt quantity = 0 thay vì delete

---

## 8. Cấu trúc UI gợi ý

```
┌─────────────────────────────────────────────┐
│ 📦 HUB SUPPLIES — Hub Quận 7                │
├─────────────────────────────────────────────┤
│ [+ Thêm vật tư mới]                         │
├─────────────────────────────────────────────┤
│ Vật tư              | SL  | Đơn vị | Hành động
├─────────────────────────────────────────────┤
│ Propeller           | 14  | chiếc | [-] [+] [Sửa]
│ Repair Kit (Motor)  | 5   | bộ    | [-] [+] [Sửa]
│ Backup Battery      | 8   | cái   | [-] [+] [Sửa]
└─────────────────────────────────────────────┘

Nút thao tác:
- [-]: Giảm 1
- [+]: Tăng 1
- [Sửa]: Mở dialog cập nhật
- [Xóa]: Xóa bản ghi
```

---

## 9. Error Handling — Xử lý lỗi

```javascript
async function handleSupplyAction(action, supplyId, data) {
  try {
    const response = await fetch(
      `/api/v1/technician/supplies/${supplyId}/${action}`,
      {
        headers: { 'Authorization': `Bearer ${token}` },
        ...data
      }
    );

    if (!response.ok) {
      const error = await response.json();
      switch (response.status) {
        case 400:
          showError(`Dữ liệu không hợp lệ: ${error.detail}`);
          break;
        case 403:
          showError('Bạn không có quyền truy cập vật tư này');
          break;
        case 404:
          showError('Vật tư không tồn tại');
          break;
        default:
          showError('Lỗi: ' + error.detail);
      }
      return;
    }

    const result = await response.json();
    showSuccess('Thành công!');
    // Cập nhật UI
    refreshSupplies();
  } catch (err) {
    showError('Lỗi kết nối: ' + err.message);
  }
}
```

---

## 10. Tích hợp với Maintenance Workflow (Tùy chọn)

Nếu Backend triển khai log supplies, có thể ghi:
- **Khi tạo work order** → "Dùng 1 propeller để sửa chữa"
- **Khi xong maintenance** → "Đã thay propeller, còn X chiếc"

Hiện tại chưa được implement, nhưng có thể thêm sau.

---

## 11. Cron/Worker Jobs (Backend)

Backend sẽ có worker tự động:
- 🔔 **Cảnh báo tồn kho thấp** — Tạo notification nếu quantity < threshold
- 📊 **Báo cáo hàng tồn kho** — Daily/Weekly report cho manager

FE chỉ cần hiện thông báo khi có notification từ server.

---

## 12. Các thay đổi khác tại Technician (Bug Fixes)

### 12.1 POST `/confirm-arrival` — Fix: Loại bỏ arrival_confirmed_at sớm

**Vấn đề cũ:**
- Endpoint `confirm-arrival` đang set `arrival_confirmed_at = now()` → Block package pickup flow
- `/confirmations` endpoint filter `arrival_confirmed_at IS NULL` → Packages không hiện

**Fix:**
- Loại bỏ 2 dòng set `arrival_confirmed_at` + `arrival_confirmed_by`
- `arrival_confirmed_at` giờ **chỉ** được set khi gọi `confirm-pickup` (khách thực sự nhận hàng)

**Workflow đúng:**
```
1. Drone arrive at hub
   ↓ confirm-arrival API
   → set arrived_at = now()
   → set status = COMPLETED
   → set order.status = DELIVERED_TO_HUB
   → arrival_confirmed_at = NULL (giữ trống)
   
2. Customer picks up package
   ↓ confirm-pickup API (customer endpoint)
   → set arrival_confirmed_at = now()
   → set order.status = DELIVERED
   → package xóa khỏi hub
```

**Impact:**
- ✅ Packages giờ hiện đúng trên `/confirmations` endpoint
- ✅ Package pickup flow hoạt động bình thường

**FE cần biết:**
- Sau khi `confirm-arrival`, package sẽ hiện trên `/confirmations` (chờ customer đến nhận)
- Không cần reload lại page, data sẽ update realtime hoặc trong 1 poll cycle

---

### 12.2 POST `/work-orders` — Fix: Set drone status → MAINTENANCE

**Vấn đề cũ:**
- Tạo work order không set drone status → drone vẫn ở AVAILABLE
- Technician nhầm tưởng drone available khi thực tế đang sửa chữa

**Fix:**
- Khi tạo work order → tự động set `drone.status = MAINTENANCE`
- Khi work order đóng → set lại `drone.status = AVAILABLE`

**Workflow:**
```
1. Technician tạo work order cho drone
   → POST /work-orders
   → drone.status = MAINTENANCE
   → Drone không thể dispatch
   
2. Maintenance xong
   → Close work order
   → drone.status = AVAILABLE
   → Drone sẵn sàng dispatch lại
```

**Response:**
```json
{
  "id": 101,
  "drone_id": "28",
  "status": "Open",
  "created_at": "2026-10-18T16:00:00"
}
```

**FE cần biết:**
- Sau tạo work order, drone sẽ mất khỏi "Available drones" list
- Drone sẽ xuất hiện ở "Maintenance" section
- Technician không thể dispatch drone này

---

### 12.3 PATCH `/drones/{drone_id}/status` — Fix: Validate pending alerts

**Vấn đề cũ:**
- Có pending maintenance alerts nhưng vẫn set drone status = AVAILABLE
- Drone bay lại khi chưa sửa chữa xong → Nguy hiểm

**Fix:**
- Trước khi cho phép MAINTENANCE → AVAILABLE:
  - Check xem còn pending `MaintenanceAlert` không
  - Nếu có → reject với status 409
  - Nếu không → allow chuyển trạng thái

**Request:**
```bash
PATCH /drones/28/status
{
  "status": "AVAILABLE"
}
```

**Response (200) — Nếu OK:**
```json
{
  "id": 28,
  "status": "AVAILABLE",
  "updated_at": "2026-10-18T16:30:00"
}
```

**Response (409) — Nếu có alert pending:**
```json
{
  "detail": "Không thể chuyển sang AVAILABLE: còn 2 maintenance alert pending. Vui lòng resolve trước."
}
```

**FE cần biết:**
- Nếu gặp 409, hiện dialog:
  - "⚠️ Drone có maintenance alert chưa xong"
  - "Vui lòng resolve tất cả alert trước khi set AVAILABLE"
  - Nút: "Xem alerts" → Link tới maintenance alerts page

---

### 12.4 GET `/maintenance-alerts` — Fix: Filter by technician's hub

**Vấn đề cũ:**
- List maintenance alerts hiện **tất cả** alert trong DB
- Technician từ Hub A thấy alert của Hub B (bảo mật issue)

**Fix:**
- Technician chỉ thấy alert của drones tại hub của mình
- Admin vẫn thấy tất cả (có thể filter by hub)

**Query được thêm:**
```
WHERE Drone.current_hub_id == user.hub_id
```

**Request:**
```bash
# Technician — tự động filter theo hub
GET /maintenance-alerts

# Admin — lấy tất cả (or filter by hub)
GET /maintenance-alerts?hub_id=2
```

**Response:**
```json
[
  {
    "id": 5001,
    "drone_id": "28",
    "source": "Incident",
    "title": "Sự cố chuyến bay MSN-20261018-0001 — Medium severity",
    "status": "Pending",
    "created_at": "2026-10-18T10:23:45"
  }
]
```

**FE cần biết:**
- Technician sẽ chỉ thấy alert của hub mình
- Alert tự động filter, không cần code gì thêm

---

### 12.5 Battery Lifecycle & last_charged_at (Feature)

**Thêm:**
- Cột `last_charged_at` trong Battery model
- Auto-set khi battery charge = 100% (từ charging worker)
- GET `/batteries` trả `lifecycle_status` + `days_until_expiry`

**Response:**
```json
{
  "id": "BAT-001",
  "drone_id": "28",
  "charge_level_pct": 85,
  "capacity_wh": 150.0,
  "last_charged_at": "2026-10-18T08:00:00",
  "lifecycle_status": "Active",
  "days_until_expiry": 720,
  "status": "In Use"
}
```

**lifecycle_status:**
- `Active` — Pin còn sử dụng được (< 730 ngày)
- `Expired` — Pin quá hạn (>= 730 ngày) → Cảnh báo thay pin

**FE cần biết:**
- Hiện `days_until_expiry` trên battery detail
- Alert nếu `days_until_expiry < 30` → "Pin sắp hết tuổi"
- Alert nếu `lifecycle_status == Expired` → "Cần thay pin ngay"

---

## 13. Tóm tắt tất cả thay đổi Technician

| Tính năng | Status | Impact |
|---|---|---|
| Hub Supplies CRUD | ✅ New | Quản lý vật tư tại hub |
| confirm-arrival fix | ✅ Fix | Package pickup flow hoạt động |
| work-orders status | ✅ Fix | Drone chuyển MAINTENANCE khi tạo WO |
| drone status validation | ✅ Fix | Không cho MAINTENANCE→AVAILABLE nếu alert pending |
| maintenance-alerts hub filter | ✅ Fix | Technician chỉ xem alert của hub mình |
| Battery lifecycle | ✅ New | Theo dõi tuổi thọ pin, `last_charged_at` |

---

## 14. Migration & Setup

**Chạy migration để thêm cột + table:**
```bash
python -m app.shared.database.migrate_battery_lifecycle
```

**Điều này sẽ:**
- ✅ Thêm cột `last_charged_at` vào table `batteries`
- ✅ Tạo table `hub_supplies` (id, hub_id, item_name, quantity, unit, created_at, updated_at)
- ✅ Tạo index trên `hub_supplies(hub_id)`

---

## 15. Testing Checklist

- [ ] GET `/supplies` — Hiện danh sách vật tư
- [ ] POST `/supplies` — Thêm vật tư mới
- [ ] PATCH `/supplies/{id}/adjust?delta=-2` — Giảm số lượng
- [ ] GET `/batteries` — Hiện lifecycle_status + days_until_expiry
- [ ] POST `/confirm-arrival` → GET `/confirmations` — Packages hiện (không bị block)
- [ ] POST `/work-orders` — Drone chuyển MAINTENANCE
- [ ] PATCH `/drones/{id}/status` with pending alert — Rejected (409)
- [ ] GET `/maintenance-alerts` (as Technician) — Chỉ xem alert của hub mình

