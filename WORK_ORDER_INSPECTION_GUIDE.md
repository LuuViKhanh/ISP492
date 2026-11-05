# Work Order & Inspection API Guide

## Tổng quan

Hệ thống Work Order được sử dụng để **theo dõi và quản lý công việc bảo trì drone**. Kỹ thuật viên tạo work order khi phát hiện vấn đề, sau đó cập nhật trạng thái và kết quả kiểm tra.

---

## 1. Lifecycle của Work Order

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│   Pending   │ --> │ In Progress  │ --> │   Completed   │
└─────────────┘     └──────────────┘     └───────────────┘
     (Mới tạo)      (Đang sửa chữa)      (Hoàn thành)
```

**Các trạng thái:**
- `Pending` - Work order vừa được tạo, chưa bắt đầu
- `In Progress` - Kỹ thuật viên đang tiến hành sửa chữa
- `Completed` - Sửa chữa xong, drone sẵn sàng

---

## 2. API Endpoints

### 2.1 Tạo Work Order
**Endpoint:** `POST /api/v1/technician/fleet/work-orders`

**Request:**
```json
{
  "drone_id": "DRN-0013",
  "battery_id": "BAT-0004",           // (optional) Chỉ có nếu lỗi liên quan pin
  "issue_description": "Mất tín hiệu quá 3 giây",
  "alert_id": null                    // (optional) Nếu được tạo từ maintenance alert
}
```

**Response (201 Created):**
```json
{
  "id": 15,
  "drone_id": "DRN-0013",
  "battery_id": "BAT-0004",
  "technician_id": "ed3f5204-7799-43b7-9bc8-8c6c0090b28e",
  "issue_description": "Mất tín hiệu quá 3 giây",
  "status": "Pending",
  "created_at": "2026-11-05T02:40:11.913366Z",
  "alert_id": null,
  "action_taken": null,
  "resolved_at": null,
  "diagnosis": null,
  "resulting_drone_status": null
}
```

**Lưu ý:**
- `battery_id` chỉ cần khi lỗi liên quan pin (pin hỏng, pin không sạc...)
- Nếu không chọn battery, để `null`
- Drone sẽ được tự động chuyển sang trạng thái `MAINTENANCE`
- Pin (nếu có) sẽ được đánh dấu trạng thái `In Use` và không được dùng cho mission khác

---

### 2.2 Lấy danh sách Work Order

**Endpoint:** `GET /api/v1/technician/fleet/work-orders`

**Response:**
```json
{
  "total": 5,
  "items": [
    {
      "id": 15,
      "drone_id": "DRN-0013",
      "status": "In Progress",
      "issue_description": "Mất tín hiệu quá 3 giây",
      "technician_id": "ed3f5204-...",
      "created_at": "2026-11-05T02:40:11.913366Z",
      ...
    }
  ]
}
```

---

### 2.3 Lấy chi tiết Work Order

**Endpoint:** `GET /api/v1/technician/fleet/work-orders/{work_order_id}`

**Response:**
```json
{
  "id": 15,
  "drone_id": "DRN-0013",
  "battery_id": "BAT-0004",
  "technician_id": "ed3f5204-7799-43b7-9bc8-8c6c0090b28e",
  "issue_description": "Mất tín hiệu quá 3 giây",
  "action_taken": "Thay thế module truyền thông",
  "status": "In Progress",
  "resolved_at": null,
  "created_at": "2026-11-05T02:40:11.913366Z",
  "diagnosis": "Module truyền thông bị hỏng",
  "resulting_drone_status": null,
  "alert_id": null
}
```

---

## 3. Inspection & Completion (🔴 Quan trọng nhất cho FE)

### 3.1 Cập nhật Inspection & Hoàn thành Work Order

**Endpoint:** `PATCH /api/v1/technician/fleet/work-orders/{work_order_id}/inspection`

**Mục đích:** Kỹ thuật viên cập nhật kết quả kiểm tra và **hoàn thành work order**.

**Request:**
```json
{
  "status": "Completed",
  "action_taken": "Thay thế module truyền thông mới",
  "diagnosis": "Module truyền thông bị hỏng do quá nhiệt",
  "resulting_drone_status": "Ready"
}
```

**Giải thích từng field:**

| Field | Bắt buộc | Kiểu | Mô tả |
|-------|---------|------|-------|
| `status` | ✅ | string | Trạng thái muốn chuyển sang. Giá trị hợp lệ: `"In Progress"`, `"Completed"` |
| `action_taken` | ✅ | string | Công việc đã làm (không được trống, không được null) |
| `diagnosis` | ❌ | string | Chẩn đoán - cái gì bị hỏng và tại sao (optional) |
| `resulting_drone_status` | ❌ | string | Trạng thái drone sau sửa. Giá trị: `"Ready"`, `"Maintenance"`, `"Retired"` (optional) |

**Response (200 OK):**
```json
{
  "id": 15,
  "drone_id": "DRN-0013",
  "battery_id": "BAT-0004",
  "technician_id": "ed3f5204-7799-43b7-9bc8-8c6c0090b28e",
  "issue_description": "Mất tín hiệu quá 3 giây",
  "action_taken": "Thay thế module truyền thông mới",
  "status": "Completed",
  "resolved_at": "2026-11-05T03:15:22.456789Z",
  "diagnosis": "Module truyền thông bị hỏng do quá nhiệt",
  "resulting_drone_status": "Ready",
  "created_at": "2026-11-05T02:40:11.913366Z",
  "alert_id": null
}
```

**Lưu ý quan trọng:**
- `action_taken` **bắt buộc** và **không được để trống/rỗng**
- Gửi `"."` cũng được chấp nhận nhưng **không nên** (không có ý nghĩa)
- Khi `status = "Completed"`:
  - Backend sẽ **tự động xoá `battery.work_order_id`** nếu có pin
  - Drone sẽ được cập nhật trạng thái (nếu `resulting_drone_status` được cung cấp)
  - Maintenance alert sẽ được tự động acknowledge (nếu có)

**Status HTTP có thể:**
- `200 OK` - Thành công
- `400 Bad Request` - `action_taken` trống hoặc không hợp lệ
- `404 Not Found` - Work order không tồn tại
- `500 Internal Server Error` - Lỗi server (check logs)

---

### 3.2 Các tình huống gửi Inspection Update

#### **Tình huống 1: Đánh dấu work order là In Progress**
```json
{
  "status": "In Progress",
  "action_taken": "Bắt đầu kiểm tra drone"
}
```

#### **Tình huống 2: Hoàn thành - Drone sửa xong, sẵn sàng**
```json
{
  "status": "Completed",
  "action_taken": "Thay thế module truyền thông",
  "diagnosis": "Module bị hỏng do quá nhiệt",
  "resulting_drone_status": "Ready"
}
```

#### **Tình huống 3: Hoàn thành - Drone cần bảo trì thêm**
```json
{
  "status": "Completed",
  "action_taken": "Phát hiện pin bị hỏng, cần thay mới",
  "diagnosis": "Pin không sạc được, công suất chỉ còn 20%",
  "resulting_drone_status": "Maintenance"
}
```

#### **Tình huống 4: Hoàn thành - Drone không thể sửa chữa**
```json
{
  "status": "Completed",
  "action_taken": "Kiểm tra toàn bộ hệ thống, không thể sửa",
  "diagnosis": "Hệ thống điều khiển bị hỏng vĩnh viễn",
  "resulting_drone_status": "Retired"
}
```

---

## 4. Maintenance Record (Ghi chép bảo trì)

`Maintenance Record` là **bản ghi chi tiết về công việc bảo trì đã hoàn thành**.

### 4.1 Lấy danh sách Maintenance Records của Drone

**Endpoint:** `GET /api/v1/technician/fleet/maintenance-records/{drone_id}`

**Response:**
```json
[
  {
    "id": 15,
    "work_order_id": 15,
    "drone_id": "DRN-0013",
    "performed_by": "ed3f5204-7799-43b7-9bc8-8c6c0090b28e",
    "title": "Bảo trì định kỳ",
    "diagnosis": "Module truyền thông bị hỏng",
    "corrective_action": "Thay thế module truyền thông mới",
    "resulting_drone_status": "Ready",
    "inspection_items": [
      {"item_name": "Kiểm tra cánh quạt", "is_completed": true},
      {"item_name": "Test động cơ", "is_completed": true}
    ],
    "created_at": "2026-11-05T03:15:22.456789Z"
  }
]
```

---

## 5. Trình tự hoàn chỉnh (Chính xác nhất)

### **Bước 1: Tạo Work Order**
```bash
POST /api/v1/technician/fleet/work-orders
{
  "drone_id": "DRN-0013",
  "battery_id": "BAT-0004",
  "issue_description": "Mất tín hiệu quá 3 giây"
}
```
→ Response: `work_order_id = 15`

### **Bước 2: Cập nhật Inspection để Complete**
```bash
PATCH /api/v1/technician/fleet/work-orders/15/inspection
{
  "status": "Completed",
  "action_taken": "Thay thế module truyền thông",
  "diagnosis": "Module bị hỏng do quá nhiệt",
  "resulting_drone_status": "Ready"
}
```
→ Response: `status = "Completed"`

**Xong.** Không cần API khác.

---

## 6. Mapping trạng thái Drone

Khi hoàn thành work order, `resulting_drone_status` có thể là:

| Trạng thái | Ý nghĩa | Có thể bay được? |
|-----------|---------|-----------------|
| `Ready` | Drone sẵn sàng, không có vấn đề | ✅ Có |
| `Maintenance` | Drone có vấn đề nhưng có thể tạm bay | ⚠️ Có (với giới hạn) |
| `Retired` | Drone bị hỏng, không thể sửa | ❌ Không |
| `Charging` | Drone đang sạc pin | ⏳ Chờ |
| `In Transit` | Drone đang bay | 🛸 Đang bay |

---

## 7. Thường gặp (FAQ)

### Q: Tại sao API trả 500 khi FE gửi Complete nhưng Swagger lại được?

**A:** Có thể là:
1. Work order khác nhau (FE gửi work_order_id khác Swagger)
2. Request body khác (FE gửi field nào đó sai)
3. Server logs có chi tiết lỗi thực tế

**Kiểm tra:** Check `action_taken` - phải có giá trị, không được null hoặc trống.

### Q: action_taken phải là gì? Có độ dài tối thiểu không?

**A:** 
- **Không có yêu cầu độ dài tối thiểu** (thậm chí `"."` cũng được)
- Nhưng **phải có giá trị** (không được null/trống)
- Nên gửi mô tả có ý nghĩa (ví dụ: "Thay thế module truyền thông mới")

### Q: Khi Complete, pin sẽ ra sao?

**A:**
- Backend sẽ **tự động xoá `battery.work_order_id`**
- Pin sẽ không còn trạng thái `In Use`
- Pin có thể được dùng cho mission khác

### Q: Có cần lưu Maintenance Record trước khi Complete không?

**A:** **KHÔNG cần.** 
- `Maintenance Record` chỉ là bản ghi lịch sử (DTO wrapper của WorkOrder)
- Việc gọi PATCH `/inspection` sẽ **tự động tạo maintenance record**
- Không cần tạo riêng

### Q: Drone status sẽ thay đổi sao?

**A:**
- Nếu `resulting_drone_status = "Ready"` → Drone chuyển sang `AVAILABLE`
- Nếu `resulting_drone_status = "Maintenance"` → Drone ở `MAINTENANCE`
- Nếu `resulting_drone_status = "Retired"` → Drone ở `RETIRED`

---

## 8. Error Handling

| Status | Message | Giải pháp |
|--------|---------|----------|
| 400 | "Phải cung cấp action_taken khi hoàn thành Work Order" | Gửi `action_taken` với giá trị có ý nghĩa |
| 404 | "Work order not found" | Kiểm tra `work_order_id` có tồn tại không |
| 500 | Internal Server Error | Check server logs, liên hệ BE |

---

## 9. Code Example (JavaScript/Fetch)

```javascript
// 1. Tạo Work Order
const createWorkOrder = async (droneId, batteryId, issueDesc) => {
  const response = await fetch(
    'https://api.droneoptai.id.vn/api/v1/technician/fleet/work-orders',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        drone_id: droneId,
        battery_id: batteryId,
        issue_description: issueDesc
      })
    }
  );
  return response.json();
};

// 2. Complete Work Order
const completeWorkOrder = async (workOrderId, actionTaken, diagnosis, resultingStatus) => {
  const response = await fetch(
    `https://api.droneoptai.id.vn/api/v1/technician/fleet/work-orders/${workOrderId}/inspection`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        status: 'Completed',
        action_taken: actionTaken,
        diagnosis: diagnosis || undefined,
        resulting_drone_status: resultingStatus || undefined
      })
    }
  );
  
  if (!response.ok) {
    const error = await response.json();
    console.error('Error:', error);
    return null;
  }
  
  return response.json();
};

// 3. Sử dụng
const wo = await createWorkOrder('DRN-0013', 'BAT-0004', 'Mất tín hiệu');
console.log('Work Order created:', wo.id);

const result = await completeWorkOrder(
  wo.id,
  'Thay thế module truyền thông',
  'Module bị hỏng do quá nhiệt',
  'Ready'
);
console.log('Work Order completed:', result);
```

---

## 10. Liên hệ & Debug

Nếu có lỗi:
1. Check request body - đúng format JSON không?
2. Check `action_taken` - có giá trị không?
3. Check `work_order_id` - có tồn tại không?
4. Check server logs - có error gì không?
5. Liên hệ BE team với:
   - Work Order ID
   - Request payload bạn gửi
   - Error message nhận được
   - Request URL chính xác

---

**Last updated:** 18/10/2026
**API Base URL:** https://api.droneoptai.id.vn
