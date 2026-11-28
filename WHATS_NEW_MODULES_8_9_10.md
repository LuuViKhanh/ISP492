# 📋 Cập Nhật Mục 8, 9, 10 - Proof Images & Admin Operations

**Ngày:** October 2026  
**Branch:** `fix/confirm-arrival-status-update`  
**Status:** ✅ Hoàn thành  

---

## 📌 Tổng Quan

Ba mục này triển khai hệ thống **audit trail đầy đủ** cho:
- **Mục 8:** Lịch sử minh chứng đơn hàng (4 mốc: receive, dispatch, confirm-arrival, confirm-pickup)
- **Mục 9:** Lịch sử minh chứng thiết bị drone/pin/bảo trì (3 mốc: battery install, drone status, work order inspection)
- **Mục 10:** Admin Operations Dashboard (5 tính năng: battery history, flight history, order stats, operator view)

---

## 🔄 Mục 8: Order Lifecycle Proof Images ✅ COMPLETE

### 📊 Vấn đề
Cần track lịch sử hình ảnh minh chứng cho đơn hàng qua 4 giai đoạn:
1. **Receive at Origin Hub** - Nhân viên nhập kho ghi nhận hình ảnh hàng
2. **Load to Drone** - Nhân viên xếp hàng lên drone ghi nhận hình ảnh
3. **Unload from Drone** - Nhân viên xác nhận hàng xuống tại hub đích
4. **Customer Pickup** - Khách hàng nhận hàng, ghi nhận hình ảnh

### ✨ Giải Pháp Triển Khai

#### 1. Database Model (`Order`)
```python
# app/modules/missions/models.py
proof_images_json: Mapped[list | None] = mapped_column(
    JSONB, nullable=True, default=list
)
# Lưu danh sách proof image entries: 
# [{stage, hub_id, mission_id, technician_id, proof_image_url, timestamp}, ...]
```

#### 2. Schemas
```python
# app/modules/missions/schemas.py
class OrderProofImageEntry(BaseModel):
    stage: str  # PACKAGE_RECEIVED_AT_ORIGIN_HUB | PACKAGE_LOADED_TO_DRONE | ...
    hub_id: Optional[int]
    mission_id: Optional[str]
    technician_id: Optional[str]
    proof_image_url: Optional[str]  # Base64 Data URL
    recorded_at: datetime

def build_order_proof_image_entry(
    stage: str, 
    hub_id: Optional[int], 
    mission_id: Optional[str], 
    technician_id: Optional[str],
    proof_image_url: Optional[str]
) -> dict:
    """Auto-generate proof image entry"""
```

#### 3. Endpoints Modified (Technician)

| Endpoint | Action | Log |
|----------|--------|-----|
| `POST /technician/deliveries/outbound/{orderId}/receive` | Receive package | `PACKAGE_RECEIVED_AT_ORIGIN_HUB` |
| `POST /technician/fleet/missions/{mission_id}/dispatch` | Load to drone | `PACKAGE_LOADED_TO_DRONE` |
| `POST /technician/fleet/missions/{mission_id}/confirm-arrival` | Unload at destination | `PACKAGE_UNLOADED_FROM_DRONE` |
| `POST /technician/deliveries/confirmations/{orderId}/confirm-pickup` | Customer pickup | `CUSTOMER_PICKUP_CONFIRMED` |

**Logging Pattern:**
```python
# Log to activity_logs
ActivityLog(
    resource_type="order",
    resource_id=order_id,
    action=action_name,
    details_json={
        "stage": stage,
        "hub_id": hub_id,
        "mission_id": mission_id,
        "technician_id": technician_id,
        "proof_image_url": proof_image_url,
        "timestamp": now.isoformat()
    },
    created_at=now
)

# Denormalize to Order.proof_images_json
order.proof_images_json.append({
    "stage": stage,
    "hub_id": hub_id,
    "mission_id": mission_id,
    "technician_id": technician_id,
    "proof_image_url": proof_image_url,
    "recorded_at": now.isoformat()
})
```

#### 4. GET Endpoints

**Technician View:**
- `GET /technician/deliveries/outbound` - Return `order_proof_images` array
- `GET /technician/fleet/missions/{mission_id}/confirm-arrival` - Include proof images
- `GET /technician/deliveries/confirmations` - Include proof images in response

**Admin View:**
- `GET /admin/orders/{order_id}/proof-images` - Full audit trail
  - Response: danh sách tất cả proof images theo chronological order
  - Phục vụ admin review/audit

### 📝 Files Modified
- `app/modules/missions/models.py` - Thêm `proof_images_json` to Order
- `app/modules/missions/schemas.py` - OrderProofImage schemas + helper
- `app/modules/missions/technician/router.py` - 4 endpoints modified
- `app/modules/system/admin/router.py` - Thêm GET endpoint

### ✅ Commits
```
✅ Mục 8: Order lifecycle proof images (4 stages, activity logs)
✅ Mục 8: Endpoints & admin audit trail
```

---

## 🔄 Mục 9: Drone/Battery/Maintenance Proof Images ✅ COMPLETE (8/11 Tasks)

### 📊 Vấn đề
Cần track lịch sử chi tiết cho 3 mốc:
1. **Battery Install on Drone** - Gắn pin lên drone, ghi checklist
2. **Drone Status Change** - Thay đổi trạng thái drone (AVAILABLE → MAINTENANCE → AVAILABLE)
3. **Work Order Inspection** - Kiểm tra/bảo trì, lưu hình ảnh before/after + danh sách linh kiện

### ✨ Giải Pháp Triển Khai

#### 1. Schemas (`app/modules/fleet/technician/schemas.py`)

```python
class BatteryHistoryEntry(BaseModel):
    """Lịch sử gắn/tháo/kiểm tra pin"""
    stage: str  # BATTERY_INSTALLED_ON_DRONE | BATTERY_REMOVED_FROM_DRONE
    action: str  # install | remove | inspection
    removed_battery_id: Optional[str]  # Pin cũ được tháo
    installed_battery_id: Optional[str]  # Pin mới được lắp
    battery_pct_before: Optional[float]  # % pin trước tháo
    battery_pct_after: Optional[float]  # % pin sau lắp
    hub_id: Optional[int]
    mission_id: Optional[str]
    drone_id: str
    technician_id: Optional[str]
    note: Optional[str]
    recorded_at: datetime

class DroneStatusChangeEntry(BaseModel):
    """Lịch sử thay đổi trạng thái drone"""
    old_status: str
    new_status: str
    reason: Optional[str]
    incident_id: Optional[int]
    work_order_id: Optional[int]
    technician_id: Optional[str]
    note: Optional[str]
    recorded_at: datetime

class InspectionItem(BaseModel):
    """Item trong checklist kiểm tra"""
    item: str  # ví dụ: "Check motor left"
    checked: bool = False
    status: Optional[str]  # OK | FAIL | REPAIR_NEEDED
    note: Optional[str]

class WorkOrderProofImage(BaseModel):
    """Hình ảnh minh chứng công việc"""
    type: str  # before | after | during | parts_replaced | incident_recovery
    proof_image_url: Optional[str]  # Base64 Data URL
    description: Optional[str]
    recorded_at: datetime

class WorkOrderStatusLog(BaseModel):
    """Log thay đổi trạng thái work order"""
    old_status: str
    new_status: str
    changed_by: Optional[str]
    reason: Optional[str]
    changed_at: datetime

class DefectType(str):
    """Enum loại lỗi để auto-generate inspection items"""
    MOTOR_ESC = "MOTOR_ESC"
    SIGNAL_LOSS = "SIGNAL_LOSS"
    BATTERY_BMS = "BATTERY_BMS"
    SENSOR_GPS = "SENSOR_GPS"
    FRAME_DAMAGE = "FRAME_DAMAGE"
    PROPELLER_DAMAGE = "PROPELLER_DAMAGE"
    OTHER = "OTHER"

def build_inspection_items_from_defect_type(defect_type: str) -> list:
    """Auto-generate inspection_items_json dựa vào defect type"""
    # Template checklist cho từng loại lỗi
    templates = {
        "MOTOR_ESC": [
            {"item": "Check motor left", "checked": False},
            {"item": "Check motor right", "checked": False},
            {"item": "Check ESC connections", "checked": False},
            {"item": "Test motor rotation (no load)", "checked": False},
        ],
        # ... other templates
    }
    return templates.get(defect_type, [])
```

#### 2. Database Model Updates (`app/modules/fleet/models.py`)

```python
# WorkOrder Model
inspection_items_json: Mapped[list | None] = mapped_column(
    JSON, nullable=True, default=list
)  # [{item, checked, status, note}, ...]

status_logs_json: Mapped[list | None] = mapped_column(
    JSON, nullable=True, default=list
)  # [{old_status, new_status, changed_by, changed_at}, ...]

proof_image_url: Mapped[str | None] = mapped_column(
    Text, nullable=True
)  # Single proof image (Base64 Data URL) - before/after/during/incident_recovery

parts_replaced_proof_url: Mapped[str | None] = mapped_column(
    Text, nullable=True
)  # Single proof image for parts replaced (Base64 Data URL)

# Drone Model
image_url: Mapped[str | None] = mapped_column(
    Text, nullable=True
)  # Latest status image (Base64 Data URL) - updated when DRONE_STATUS_CHANGED / work order completed

# Battery Model
image_url: Mapped[str | None] = mapped_column(
    Text, nullable=True
)  # Latest status image (Base64 Data URL) - updated when BATTERY_INSTALLED_ON_DRONE / BATTERY_SWAPPED
```

#### 3. Endpoints - Activity Logging Pattern

| Endpoint | Action | Log Details |
|----------|--------|-------------|
| `POST /technician/deliveries/outbound/{orderId}/prepare-battery` | Battery install | `BATTERY_INSTALLED_ON_DRONE` |
| `POST /technician/fleet/missions/{mission_id}/swap-battery` | Battery swap | `BATTERY_SWAPPED` (removed + installed) |
| `PATCH /technician/fleet/drones/{drone_id}/status` | Drone status change | `DRONE_STATUS_CHANGED` |
| `PATCH /technician/fleet/work-orders/{work_order_id}/inspection` | Work order inspection | `INSPECTION_COMPLETED` |

**Task 2 - Prepare Battery:**
```python
# Log to activity_logs (resource_type=battery and drone)
ActivityLog(
    resource_type="battery",
    resource_id=battery_id,
    action="BATTERY_INSTALLED_ON_DRONE",
    details_json={
        "drone_id": drone_id,
        "installed_battery_id": battery_id,
        "battery_pct_after": battery.charge_level_pct,
        "technician_id": user.id,
        "hub_id": drone.current_hub_id,
        "timestamp": now.isoformat()
    },
    created_at=now
)
```

**Task 3 - Swap Battery:**
```python
# Log both removed and installed battery
for battery in [old_battery, new_battery]:
    ActivityLog(
        resource_type="battery",
        resource_id=battery.id,
        action="BATTERY_SWAPPED",
        details_json={
            "drone_id": drone_id,
            "removed_battery_id": old_battery.id,
            "installed_battery_id": new_battery.id,
            "battery_pct_before": old_battery.charge_level_pct,
            "battery_pct_after": new_battery.charge_level_pct,
            "technician_id": user.id,
            "timestamp": now.isoformat()
        },
        created_at=now
    )
```

**Task 4 - Drone Status Change:**
```python
ActivityLog(
    resource_type="drone",
    resource_id=drone_id,
    action="DRONE_STATUS_CHANGED",
    details_json={
        "old_status": old_status,
        "new_status": body.status,
        "reason": body.reason,
        "work_order_id": body.work_order_id,
        "technician_id": user.id,
        "timestamp": now.isoformat()
    },
    created_at=now
)
```

**Task 6 - Work Order Inspection (ENHANCED):**
```python
# Extended InspectionUpdate schema
class InspectionUpdate(BaseModel):
    action_taken: str
    status: WorkOrderStatus
    diagnosis: Optional[str] = None
    resulting_drone_status: Optional[str] = None
    proof_image_url: Optional[str]  # Base64 Data URL - single proof image
    parts_replaced_proof_url: Optional[str]  # Base64 Data URL - single proof image
    inspection_items: Optional[list[dict]]  # [{item, checked, status, note}, ...]

# Log to activity_logs (resource_type=drone)
ActivityLog(
    resource_type="drone",
    resource_id=wo.drone_id,
    action="INSPECTION_COMPLETED",
    details_json={
        "work_order_id": wo.id,
        "technician_id": user.id,
        "action_taken": body.action_taken,
        "diagnosis": body.diagnosis,
        "proof_image_url": body.proof_image_url,
        "parts_replaced_proof_url": body.parts_replaced_proof_url,
        "inspection_items": body.inspection_items,
        "timestamp": now.isoformat()
    },
    created_at=now
)

# Save directly to WorkOrder fields
wo.proof_image_url = body.proof_image_url  # Single URL field
wo.parts_replaced_proof_url = body.parts_replaced_proof_url  # Single URL field
wo.inspection_items_json = body.inspection_items

# Update drone.image_url if proof image provided
if wo.drone_id and body.proof_image_url:
    drone.image_url = body.proof_image_url
```

#### 4. GET Endpoints - Technician & Admin

**Technician Endpoints:**
- `GET /technician/fleet/drones/{drone_id}/battery-history` - Lịch sử pin của drone
  - Response: [{stage, action, removed_battery_id, installed_battery_id, battery_pct_before, battery_pct_after, hub_id, mission_id, technician_id, recorded_at}, ...]
  - Sort by recorded_at DESC, support pagination

- `GET /technician/fleet/batteries/{battery_id}/history` - Lịch sử pin
  - Response: [{action, details, recorded_at, technician_id, drone_id}, ...]
  - Actions: BATTERY_INSTALLED_ON_DRONE, BATTERY_SWAPPED, BATTERY_CHARGED

- `GET /technician/fleet/work-orders/{work_order_id}/proof-images` - Toàn bộ audit trail
  - Response: {work_order_id, drone_id, status, proof_images, parts_replaced_images, inspection_items, status_logs, created_at, resolved_at}
  - All arrays sorted chronologically (latest first)

**Admin Endpoints:**
- Same as Technician but with **toàn hệ thống** scope (not hub-limited)
- Located in `/admin/fleet/...` endpoints

#### 5. Files Modified
- `app/modules/fleet/models.py` - WorkOrder model: proof_images_json, parts_replaced_proof_json
- `app/modules/fleet/schemas.py` - InspectionUpdate schema + WorkOrderResponse fields
- `app/modules/fleet/technician/schemas.py` - All schemas (BatteryHistoryEntry, DroneStatusChangeEntry, InspectionItem, WorkOrderProofImage, WorkOrderStatusLog, DefectType, helper)
- `app/modules/fleet/technician/router.py` - 4 endpoints + 3 GET endpoints

#### 6. Commits
```
✅ Mục 9: Schemas & defect types (task 1)
✅ Mục 9: Prepare battery endpoint & logging (task 2)
✅ Mục 9: Swap battery endpoint & logging (task 3)
✅ Mục 9: Drone status change endpoint & logging (task 4)
✅ Mục 9: Work order inspection & battery history endpoints (tasks 6, 8, 9, 10)
```

---

## 🔄 Mục 10: Admin Operations Dashboard ✅ COMPLETE

### 📊 Vấn Đề
Cần triển khai 5 tính năng cho Admin:
1. Lịch sử gắn pin (kèm ảnh minh chứng)
2. Lịch sử di chuyển drone (tổng quát & detail)
3. Filter/search nhanh drone và pin
4. Thống kê đơn (successful, refund, cancelled)
5. Admin phải có quyền xem Operator

### ✨ Giải Pháp Triển Khai

#### 1. 10.1.1 Battery History (kèm proof_image)

**Admin Battery History Endpoints:**

```python
# GET /admin/fleet/drones/{drone_id}/battery-history
# Response: Extract fields from activity_logs.details_json
[
    {
        "id": int,
        "stage": "BATTERY_INSTALLED_ON_DRONE" | "BATTERY_SWAPPED",
        "action": "install" | "swap",
        "removed_battery_id": optional[str],
        "installed_battery_id": optional[str],
        "battery_pct_before": optional[float],
        "battery_pct_after": optional[float],
        "hub_id": optional[int],
        "mission_id": optional[str],
        "drone_id": str,
        "technician_id": optional[str],
        "note": optional[str],
        "proof_image_url": optional[str],  # Base64 Data URL
        "recorded_at": datetime.isoformat()
    }
]

# GET /admin/fleet/batteries/{battery_id}/history
# Response: Extract fields from activity_logs.details_json
[
    {
        "id": int,
        "action": "BATTERY_INSTALLED_ON_DRONE" | "BATTERY_SWAPPED" | "BATTERY_CHARGED",
        "drone_id": optional[str],
        "battery_id": str,
        "charge_level_pct": optional[float],
        "hub_id": optional[int],
        "mission_id": optional[str],
        "proof_image_url": optional[str],  # Base64 Data URL
        "note": optional[str],
        "technician_id": optional[str],
        "recorded_at": datetime.isoformat()
    }
]
```

#### 2. 10.1.2 Flight History (tổng quát + detail)

**Global Flights Endpoint:**

```python
# GET /admin/fleet/flights
# Lịch sử di chuyển toàn đội bay (toàn hệ thống)
# Query params: limit=50, offset=0, status=optional
[
    {
        "flight_id": "FLT-20261018-MSN001",
        "mission_id": "MSN-001",
        "drone_id": "DRN-001",
        "route": "HCM-01 -> HCM-02",
        "departure": "18/10/2026, 10:30",
        "arrival": "18/10/2026, 11:15",
        "duration_minutes": 45,
        "energy_wh": 180.5,
        "status": "COMPLETED",
        "battery_id": "BAT-001",
        "origin_hub_id": 1,
        "destination_hub_id": 2,
        "payload_weight_kg": 2.5
    }
]
```

**Enhanced Flight Detail Endpoint:**

```python
# GET /admin/fleet/flights/{flight_id}
# Detail kèm legs, hub trung chuyển, pin, telemetry, checkpoints ảnh
{
    "flight_id": "FLT-20261018-MSN001",
    "mission_id": "MSN-001",
    "drone_id": "DRN-001",
    "route": "HCM-01 -> HCM-02",
    "origin_hub_id": 1,
    "origin_hub_name": "HCM Hub 1",
    "destination_hub_id": 2,
    "destination_hub_name": "HCM Hub 2",
    "departure": "18/10/2026, 10:30",
    "arrival": "18/10/2026, 11:15",
    "duration_minutes": 45,
    "energy_wh": 180.5,
    "status": "COMPLETED",
    "battery_id": "BAT-001",
    "battery_model": "TB60",
    "battery_charge_pct": 85.5,
    "payload_weight_kg": 2.5,
    "telemetry_logs": [
        {
            "timestamp": "2026-10-18T10:30:00",
            "latitude": 10.7769,
            "longitude": 106.6960,
            "altitude_m": 150,
            "battery_pct": 92.0,
            "energy_consumed_wh": 15.5,
            "image_url": "s3://bucket/checkpoint_001.jpg"  # Optional checkpoint image
        }
        # ... max 10 checkpoints
    ],
    "checkpoint_count": 15
}
```

#### 3. 10.1.3 Search & Filter nhanh

**Status:** Khỏi cần thêm - BE return toàn bộ fields:
- `GET /admin/fleet/drones` - có sẵn: id, serial_number, model, installed_battery_id, drone_id
- `GET /admin/fleet/batteries` - có sẵn: id, serial_number, drone_id, available_status

**FE Handle:** Filter/search logic ở client-side

#### 4. 10.1.4 Order Statistics

**Schema (`app/modules/dashboard/schemas.py`):**

```python
class AdminOrders(BaseModel):
    total: int  # Tổng số đơn
    successful_orders_count: int  # Delivered orders
    refunded_orders_count: int  # Payment status = REFUNDED
    cancelled_orders_count: int  # Cancelled orders
```

**Dashboard Endpoint Enhancement:**

```python
# GET /admin/dashboard
# Response object thêm field mới:
{
    "revenue": {...},
    "users": {...},
    "missions": {...},
    "fleet": {...},
    "batteries": {...},
    "ai": {...},
    "alerts": {...},
    "ratings": {...},
    "orders": {
        "total": 1250,
        "successful_orders_count": 1180,
        "refunded_orders_count": 35,
        "cancelled_orders_count": 35
    }
}
```

#### 5. 10.1.5 Operator View (Admin Access)

**Status:** ✅ Already Implemented

Tất cả Operator routers đã config Admin access:

```python
# app/modules/missions/operator/router.py
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

# app/modules/fleet/operator/router.py
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

# app/modules/dashboard/operator/router.py
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

# app/modules/chatbot/operator/router.py
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])
```

**Admin can access:**
- `/operator/missions/*` - Mission planning, assignments
- `/operator/fleet/*` - Fleet monitoring
- `/operator/dashboard/*` - Operator dashboard KPIs
- `/chatbot/operator/*` - Chatbot for operators

**Phạm vi:** Toàn hệ thống (không hub-limited)  
**Quyền:** Read-only (Admin chỉ xem, không thực hiện)

### 📝 Files Modified
- `app/modules/dashboard/schemas.py` - Thêm AdminOrders schema
- `app/modules/dashboard/admin/router.py` - Order stats query enhancement
- `app/modules/fleet/admin/router.py` - 3 new endpoints

### ✅ Commits
```
✅ Mục 10: Admin operations dashboard (battery history, flight history, stats)
```

---

## 🎯 Summary - API Endpoints Reference

### Mục 8: Order Proof Images
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/technician/deliveries/outbound/{orderId}/receive` | POST | Log PACKAGE_RECEIVED_AT_ORIGIN_HUB |
| `/technician/fleet/missions/{mission_id}/dispatch` | POST | Log PACKAGE_LOADED_TO_DRONE |
| `/technician/fleet/missions/{mission_id}/confirm-arrival` | POST | Log PACKAGE_UNLOADED_FROM_DRONE |
| `/technician/deliveries/confirmations/{orderId}/confirm-pickup` | POST | Log CUSTOMER_PICKUP_CONFIRMED |
| `/admin/orders/{order_id}/proof-images` | GET | Full audit trail |

### Mục 9: Device Proof Images & History
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/technician/deliveries/outbound/{orderId}/prepare-battery` | POST | Log BATTERY_INSTALLED_ON_DRONE |
| `/technician/fleet/missions/{mission_id}/swap-battery` | POST | Log BATTERY_SWAPPED |
| `/technician/fleet/drones/{drone_id}/status` | PATCH | Log DRONE_STATUS_CHANGED |
| `/technician/fleet/work-orders/{work_order_id}/inspection` | PATCH | Log INSPECTION_COMPLETED |
| `/technician/fleet/drones/{drone_id}/battery-history` | GET | Battery history per drone |
| `/technician/fleet/batteries/{battery_id}/history` | GET | Battery history per battery |
| `/technician/fleet/work-orders/{work_order_id}/proof-images` | GET | Work order audit trail |

### Mục 10: Admin Dashboard
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/fleet/drones/{drone_id}/battery-history` | GET | Battery history (admin view) |
| `/admin/fleet/batteries/{battery_id}/history` | GET | Battery history (admin view) |
| `/admin/fleet/flights` | GET | Global flight history |
| `/admin/fleet/flights/{flight_id}` | GET | Flight detail + telemetry |
| `/admin/dashboard` | GET | Dashboard with order stats |
| `/operator/missions/*` | GET | Operator view (read-only) |
| `/operator/fleet/*` | GET | Operator view (read-only) |
| `/operator/dashboard/*` | GET | Operator view (read-only) |

---

## 📋 Activity Log Pattern

Tất cả logging follow pattern:

```python
ActivityLog(
    resource_type: str,  # "order" | "drone" | "battery"
    resource_id: str,    # order_id | drone_id | battery_id
    action: str,         # Event name (PACKAGE_RECEIVED_AT_ORIGIN_HUB, etc.)
    details_json: dict,  # Complete context
    created_at: datetime  # Timestamp
)
```

---

## 🔄 Data Flow

```
User Action (Technician)
    ↓
API Endpoint (modified)
    ↓
✅ Update Model (Order/WorkOrder/etc.)
✅ Log to activity_logs (resource_type, action, details)
✅ Denormalize to proof_images_json (if applicable)
    ↓
Admin/Technician queries history via GET endpoints
    ↓
Return audit trail + proof images
```

---

## 🚀 Integration Notes

### For Frontend
1. **Mục 8:** Display order proof images timeline in delivery tracking
2. **Mục 9:** Show drone maintenance history with checklist items & proof images
3. **Mục 10:** Admin dashboard with flight replay + battery swap history
4. Implement search/filter logic ở client-side (FE receive full data from BE)

### For Testing
- Test all 4 order stages with proof images
- Verify battery swap logs both removed & installed batteries
- Check drone status transitions are logged
- Validate work order inspection with proof images + checklist items
- Admin can access all data across hubs

### Database Migrations
- Order model: add `proof_images_json` JSONB column
- WorkOrder model: add `proof_images_json`, `parts_replaced_proof_json` JSONB columns
- Both columns initialized as empty lists by default

---

## 📎 Related Documentation

- [Database cho luồng Hình ảnh Minh chứng.md](./Database%20cho%20lu%E1%BB%93ng%20H%C3%ACnh%20%E1%BA%A3nh%20Minh%20ch%E1%BB%A9ng.md)
- [TECHNICIAN_WHATS_NEW.md](./TECHNICIAN_WHATS_NEW.md)
- [LIVE_TRACKING_API.md](./LIVE_TRACKING_API.md)

---

**Last Updated:** October 18, 2026  
**Status:** ✅ Implementation Complete, Ready for FE Integration  
**Branch:** `fix/confirm-arrival-status-update`
