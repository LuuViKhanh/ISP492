# Auto-Generate Inspection Checklist from Defect Type

**Branch:** `feat/proof-images-audit-trail`

**Mục đích:** Tự động sinh checklist kiểm tra từ loại lỗi (defect type) để chuẩn hóa quy trình bảo trì.

---

## 📋 Tổng Quan

Khi technician hoàn thành kiểm tra một work order, họ có thể chỉ định loại lỗi (`defect_type`) để hệ thống tự động sinh ra danh sách kiểm tra chuẩn tắc cho loại lỗi đó.

---

## 🔄 Endpoint

### PATCH `/api/v1/technician/fleet/work-orders/{id}/inspection`

**Request Body:**

```json
{
  "action_taken": "Thay motor",
  "status": "COMPLETED",
  "diagnosis": "Motor left bị hỏng",
  "resulting_drone_status": "READY_FOR_FLIGHT",
  "defect_type": "MOTOR_ESC",
  "proof_image_url": "data:image/png;base64,...",
  "parts_replaced_proof_url": "data:image/png;base64,..."
}
```

**Fields:**

| Field | Required | Kiểu | Mô tả |
|-------|----------|------|-------|
| `action_taken` | ✅ | string | Hành động đã thực hiện |
| `status` | ✅ | enum | Work order status (COMPLETED) |
| `diagnosis` | ❌ | string | Chẩn đoán lỗi |
| `resulting_drone_status` | ❌ | string | Status của drone sau sửa chữa |
| `proof_image_url` | ❌ | string | Hình ảnh proof (Base64 Data URL) |
| `parts_replaced_proof_url` | ❌ | string | Hình ảnh chứng minh thay linh kiện |
| **`defect_type`** | ❌ | string | 🔥 Loại lỗi (AUTO-GENERATE CHECKLIST) |
| `inspection_items` | ❌ | array | Custom checklist (ưu tiên hơn auto-generate) |

---

## 🎯 Supported Defect Types

### 1. **MOTOR_ESC** - Lỗi Motor & Electronic Speed Controller

```json
{
  "defect_type": "MOTOR_ESC",
  "inspection_items": [
    {"item": "Check motor left", "checked": false},
    {"item": "Check motor right", "checked": false},
    {"item": "Check ESC connections", "checked": false},
    {"item": "Test motor rotation (no load)", "checked": false}
  ]
}
```

**Áp dụng khi:** Motor không quay, ESC báo lỗi, kết nối suống

---

### 2. **SIGNAL_LOSS** - Lỗi Telemetry & Tín Hiệu

```json
{
  "defect_type": "SIGNAL_LOSS",
  "inspection_items": [
    {"item": "Check telemetry module", "checked": false},
    {"item": "Check antenna", "checked": false},
    {"item": "Test signal strength", "checked": false},
    {"item": "Verify receiver pairing", "checked": false}
  ]
}
```

**Áp dụng khi:** Mất tín hiệu telemetry, disconnect, antenna hỏng

---

### 3. **BATTERY_BMS** - Lỗi Battery Management System

```json
{
  "defect_type": "BATTERY_BMS",
  "inspection_items": [
    {"item": "Check battery voltage", "checked": false},
    {"item": "Check battery cells balance", "checked": false},
    {"item": "Inspect battery case for damage", "checked": false},
    {"item": "Test BMS communication", "checked": false}
  ]
}
```

**Áp dụng khi:** Pin báo lỗi, BMS không phản hồi, voltage không ổn định

---

### 4. **SENSOR_GPS** - Lỗi GPS & Cảm Biến

```json
{
  "defect_type": "SENSOR_GPS",
  "inspection_items": [
    {"item": "Check GPS module", "checked": false},
    {"item": "Check compass calibration", "checked": false},
    {"item": "Check barometer", "checked": false},
    {"item": "Verify IMU orientation", "checked": false}
  ]
}
```

**Áp dụng khi:** Không có GPS fix, compass không hoạt động, IMU lỗi

---

### 5. **FRAME_DAMAGE** - Lỗi Khung Nhôm

```json
{
  "defect_type": "FRAME_DAMAGE",
  "inspection_items": [
    {"item": "Inspect frame for cracks", "checked": false},
    {"item": "Check arm alignment", "checked": false},
    {"item": "Verify landing gear", "checked": false}
  ]
}
```

**Áp dụng khi:** Frame bị nứt, arm bị cong, landing gear hỏng

---

### 6. **PROPELLER_DAMAGE** - Lỗi Cánh Quạt

```json
{
  "defect_type": "PROPELLER_DAMAGE",
  "inspection_items": [
    {"item": "Inspect all 4 propellers", "checked": false},
    {"item": "Check propeller balance", "checked": false},
    {"item": "Verify propeller mounting", "checked": false}
  ]
}
```

**Áp dụng khi:** Cánh quạt bị gãy, bị móp, không cân bằng

---

### 7. **OTHER** - Lỗi Khác (Default)

```json
{
  "defect_type": "OTHER",
  "inspection_items": [
    {"item": "General inspection", "checked": false}
  ]
}
```

**Áp dụng khi:** Loại lỗi không nằm trong danh sách trên

---

## 🔄 Priority Logic

Endpoint sử dụng logic ưu tiên sau:

```python
1. Nếu body có inspection_items (custom checklist)
   → Sử dụng inspection_items do technician cung cấp
   
2. Else if body có defect_type
   → Tự động sinh inspection_items từ defect_type
   
3. Else
   → Không có inspection items (technician sẽ thêm thủ công)
```

**Ví dụ:**

```json
# Case 1: Custom checklist (ưu tiên)
{
  "defect_type": "MOTOR_ESC",
  "inspection_items": [
    {"item": "Custom item 1", "checked": false},
    {"item": "Custom item 2", "checked": false}
  ]
}
// Result: Sử dụng custom items, bỏ qua defect_type

# Case 2: Auto-generate
{
  "defect_type": "MOTOR_ESC"
}
// Result: Sinh 4 items chuẩn tắc cho MOTOR_ESC

# Case 3: Không có gì
{
  "action_taken": "Kiểm tra kỹ"
}
// Result: inspection_items_json = null
```

---

## 📋 Response Example

```json
{
  "id": 42,
  "drone_id": "DJI-M300-001",
  "work_order_id": 42,
  "action_taken": "Thay motor",
  "status": "COMPLETED",
  "resolved_at": "2026-10-18T14:30:00",
  "diagnosis": "Motor left hỏng",
  "defect_type": "MOTOR_ESC",
  "inspection_items_json": [
    {
      "item": "Check motor left",
      "checked": false
    },
    {
      "item": "Check motor right",
      "checked": false
    },
    {
      "item": "Check ESC connections",
      "checked": false
    },
    {
      "item": "Test motor rotation (no load)",
      "checked": false
    }
  ],
  "proof_image_url": "data:image/png;base64,iVBORw0KGgo...",
  "parts_replaced_proof_url": "data:image/png;base64,iVBORw0KGgo...",
  "resulting_drone_status": "READY_FOR_FLIGHT",
  "status_logs_json": [
    {
      "action": "inspection_update",
      "from_status": "IN_PROGRESS",
      "to_status": "COMPLETED",
      "changed_by": "tech-001",
      "created_at": "2026-10-18T14:30:00"
    }
  ]
}
```

---

## 📊 Activity Logging

Khi inspection hoàn thành, `defect_type` được ghi log vào `activity_logs`:

```json
{
  "resource_type": "drone",
  "resource_id": "DJI-M300-001",
  "action": "INSPECTION_COMPLETED",
  "details_json": {
    "work_order_id": 42,
    "technician_id": "tech-001",
    "action_taken": "Thay motor",
    "diagnosis": "Motor left hỏng",
    "defect_type": "MOTOR_ESC",
    "proof_image_url": "...",
    "parts_replaced_proof_url": "...",
    "inspection_items": [...],
    "resulting_drone_status": "READY_FOR_FLIGHT",
    "timestamp": "2026-10-18T14:30:00"
  }
}
```

---

## 🔧 Implementation Details

### Helper Function

**File:** `app/modules/fleet/technician/schemas.py`

```python
def build_inspection_items_from_defect_type(defect_type: str) -> list:
    """Tự động sinh inspection_items_json dựa vào loại lỗi."""
    templates = {
        "MOTOR_ESC": [
            {"item": "Check motor left", "checked": False},
            {"item": "Check motor right", "checked": False},
            {"item": "Check ESC connections", "checked": False},
            {"item": "Test motor rotation (no load)", "checked": False},
        ],
        "SIGNAL_LOSS": [
            {"item": "Check telemetry module", "checked": False},
            {"item": "Check antenna", "checked": False},
            {"item": "Test signal strength", "checked": False},
            {"item": "Verify receiver pairing", "checked": False},
        ],
        # ... other templates
    }
    return templates.get(defect_type, [{"item": "General inspection", "checked": False}])
```

### Endpoint Implementation

**File:** `app/modules/fleet/technician/router.py`

```python
# Update inspection_items_json if provided
# Priority: 1. Explicit inspection_items, 2. Auto-generate from defect_type
if body.inspection_items:
    wo.inspection_items_json = body.inspection_items
elif body.defect_type:
    # Auto-generate inspection checklist from defect type
    from app.modules.fleet.technician.schemas import build_inspection_items_from_defect_type
    wo.inspection_items_json = build_inspection_items_from_defect_type(body.defect_type)
```

---

## 🎯 Use Cases

### Use Case 1: Nhanh Chóng Tạo Checklist Chuẩn
```
Technician nhận work order lỗi motor
→ Chỉ định defect_type="MOTOR_ESC"
→ System tự động sinh 4 items kiểm tra motor
→ Technician chỉ cần check các item, không cần nhập manual
```

### Use Case 2: Custom Checklist Cho Lỗi Phức Tạp
```
Technician gặp lỗi phức tạp không nằm trong mẫu
→ Cung cấp inspection_items custom + defect_type
→ System ưu tiên sử dụng inspection_items
→ defect_type dùng để ghi log audit trail
```

### Use Case 3: Audit Trail & Traceability
```
Quản lý xem lịch sử kiểm tra drone
→ Lọc theo defect_type
→ Xem tất cả những lần lỗi MOTOR_ESC được xử lý
→ Phân tích pattern lỗi & optimize maintenance
```

---

## 📌 Schema Changes

**File:** `app/modules/fleet/schemas.py`

```python
class InspectionUpdate(BaseModel):
    action_taken: str
    status: WorkOrderStatus
    diagnosis: Optional[str] = None
    resulting_drone_status: Optional[str] = None
    proof_image_url: Optional[str] = None
    parts_replaced_proof_url: Optional[str] = None
    inspection_items: Optional[list[dict]] = None
    defect_type: Optional[str] = None  # ← NEW: Auto-generate checklist
```

---

## 🚀 Test Request

```bash
curl -X PATCH "http://localhost:8000/api/v1/technician/fleet/work-orders/42/inspection" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "action_taken": "Replaced motor bearings",
    "status": "COMPLETED",
    "diagnosis": "Motor left bearings worn out",
    "resulting_drone_status": "READY_FOR_FLIGHT",
    "defect_type": "MOTOR_ESC",
    "proof_image_url": "data:image/png;base64,iVBORw0KGgo...",
    "parts_replaced_proof_url": "data:image/png;base64,iVBORw0KGgo..."
  }'
```

---

## ✅ Benefits

| Benefit | Mô tả |
|---------|-------|
| **Chuẩn hóa** | Mỗi loại lỗi có checklist chuẩn tắc |
| **Nhanh chóng** | Technician không cần nhập checklist manual |
| **Traceability** | Ghi log defect_type cho audit trail |
| **Flexible** | Vẫn hỗ trợ custom checklist khi cần |
| **Consistency** | Đảm bảo quality kiểm tra |

---

## 📝 Commits

```
e77d0d8 - feat: auto-generate inspection checklist from defect type in work order inspection endpoint
```

---

## 🔗 Related

- [Mục 9 - Equipment Proof Images](./WHATS_NEW_MODULES_8_9_10.md)
- [Inspection Flow](./TECHNICIAN_FEATURES.md)
- [Defect Type Enum](./app/modules/fleet/technician/schemas.py)
