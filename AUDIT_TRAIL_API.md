# Audit Trail & SLA Report API

## 📋 Tổng Quan

Hai endpoint này cung cấp lịch sử đầy đủ của đơn hàng/mission và báo cáo tuân thủ SLA cho mục đích kiểm toán (audit). **Chỉ Admin/Operator mới có quyền truy cập.**

---

## 1️⃣ Audit Trail - Lịch sử Timeline Đơn Hàng

### Endpoint
```
GET /api/v1/missions/{mission_id}/audit-trail
```

### Request

| Parameter | Type | Required | Mô tả |
|-----------|------|----------|-------|
| `mission_id` | string | ✅ | ID của mission/đơn hàng cần kiểm toán |

**Headers:**
```
Authorization: Bearer <admin_or_operator_token>
```

### Response (200 OK)

```json
{
  "mission_id": "mission_123",
  "order_id": "order_456",
  "drone_id": "drone_DJI_001",
  "timeline": [
    {
      "timestamp": "2026-10-18T08:30:00",
      "event": "Order Created",
      "status": "pending_payment",
      "actor": "Customer"
    },
    {
      "timestamp": "2026-10-18T08:35:00",
      "event": "Payment Confirmed",
      "payment_status": "paid",
      "actor": "Payment System"
    },
    {
      "timestamp": "2026-10-18T09:00:00",
      "event": "Package Received at Origin Hub",
      "received_by": "tech_user_001",
      "hub_id": "hub_HN_001",
      "actor": "Technician"
    },
    {
      "timestamp": "2026-10-18T09:15:00",
      "event": "Drone Dispatched",
      "mission_status": "in_flight",
      "drone_id": "drone_DJI_001",
      "leg_count": 2,
      "actor": "Technician"
    },
    {
      "timestamp": "2026-10-18T09:20:00",
      "event": "Leg 1 Started",
      "from_hub_id": "hub_HN_001",
      "to_hub_id": "hub_HN_RELAY_01",
      "sequence_no": 1,
      "actor": "Drone"
    },
    {
      "timestamp": "2026-10-18T09:35:00",
      "event": "Leg 1 Completed",
      "sequence_no": 1,
      "actor": "Drone"
    },
    {
      "timestamp": "2026-10-18T09:40:00",
      "event": "Leg 2 Started",
      "from_hub_id": "hub_HN_RELAY_01",
      "to_hub_id": "hub_HCM_001",
      "sequence_no": 2,
      "actor": "Drone"
    },
    {
      "timestamp": "2026-10-18T10:00:00",
      "event": "Leg 2 Completed",
      "sequence_no": 2,
      "actor": "Drone"
    },
    {
      "timestamp": "2026-10-18T10:05:00",
      "event": "Drone Arrived at Destination Hub",
      "hub_id": "hub_HCM_001",
      "actor": "Drone"
    },
    {
      "timestamp": "2026-10-18T10:15:00",
      "event": "Customer Confirmed Pickup",
      "confirmed_by": "customer_cust_789",
      "actor": "Customer"
    },
    {
      "timestamp": "2026-10-19T14:00:00",
      "event": "Maintenance Completed",
      "work_order_id": "wo_2024_001",
      "issue": "Battery not charging",
      "action": "Replaced battery pack",
      "actor": "Technician"
    }
  ],
  "summary": {
    "total_events": 11,
    "first_event": "2026-10-18T08:30:00",
    "last_event": "2026-10-19T14:00:00",
    "total_duration": 29.5
  }
}
```

### Response Fields

**Root Level:**
| Field | Type | Mô tả |
|-------|------|-------|
| `mission_id` | string | ID của mission |
| `order_id` | string | ID của đơn hàng gắn với mission |
| `drone_id` | string | ID của drone thực hiện |
| `timeline` | array | Danh sách các sự kiện theo thời gian |
| `summary` | object | Tóm tắt timeline |

**Timeline Event Objects:**
| Field | Type | Mô tả |
|-------|------|-------|
| `timestamp` | datetime | Thời điểm sự kiện xảy ra (ISO 8601) |
| `event` | string | Tên sự kiện (Order Created, Drone Dispatched, etc.) |
| `actor` | string | Ai thực hiện (Customer, Technician, Drone, Payment System) |
| `status` | string | (optional) Trạng thái liên quan (order status, mission status, etc.) |
| `actor_id` | string | (optional) ID của người/hệ thống thực hiện |
| `hub_id` | string | (optional) ID hub liên quan |
| `drone_id` | string | (optional) ID drone |
| `leg_count` | number | (optional) Tổng số chặng bay |
| `sequence_no` | number | (optional) Số thứ tự chặng bay |

**Summary:**
| Field | Type | Mô tả |
|-------|------|-------|
| `total_events` | number | Tổng số sự kiện trong timeline |
| `first_event` | datetime | Thời điểm sự kiện đầu tiên |
| `last_event` | datetime | Thời điểm sự kiện cuối cùng |
| `total_duration` | number | Tổng thời gian từ đầu đến cuối (giờ) |

### Error Responses

**404 Not Found:**
```json
{
  "detail": "Mission not found"
}
```

**403 Forbidden:**
```json
{
  "detail": "Only Admin or Operator can access audit trail"
}
```

### Use Cases

✅ **Kiểm toán SLA:** Kiểm tra timeline để đảm bảo đơn hàng được giao đúng hạn  
✅ **Điều tra khiếu nại:** Xem lịch sử chi tiết khi khách hàng khiếu nại  
✅ **Compliance:** Xuất báo cáo cho bộ phận pháp lý/kiểm toán  
✅ **Troubleshooting:** Tìm nguyên nhân delay/lỗi trong quá trình giao hàng  

### Ví dụ cURL

```bash
curl -X GET "http://localhost:8000/api/v1/missions/mission_123/audit-trail" \
  -H "Authorization: Bearer admin_token_xyz"
```

---

## 2️⃣ SLA Report - Báo cáo Tuân Thủ SLA

### Endpoint
```
GET /api/v1/missions/audit/sla-report
```

### Request

| Parameter | Type | Required | Mô tả |
|-----------|------|----------|-------|
| `start_date` | string | ✅ | Ngày bắt đầu (format: YYYY-MM-DD) |
| `end_date` | string | ✅ | Ngày kết thúc (format: YYYY-MM-DD) |

**Headers:**
```
Authorization: Bearer <admin_or_operator_token>
```

### Response (200 OK)

```json
{
  "period": "2026-10-01 to 2026-10-31",
  "metrics": {
    "total_orders": 150,
    "completed_orders": 145,
    "cancelled_orders": 5,
    "on_time_orders": 142,
    "sla_compliance_percent": 94.67,
    "avg_delivery_hours": 18.5
  }
}
```

### Response Fields

| Field | Type | Mô tả |
|-------|------|-------|
| `period` | string | Khoảng thời gian báo cáo |
| `metrics.total_orders` | number | Tổng số đơn hàng trong khoảng thời gian |
| `metrics.completed_orders` | number | Số đơn hàng đã hoàn thành và được giao |
| `metrics.cancelled_orders` | number | Số đơn hàng bị hủy |
| `metrics.on_time_orders` | number | Số đơn hàng giao đúng hạn (≤ 24 giờ từ tạo đơn) |
| `metrics.sla_compliance_percent` | number | % đơn hàng tuân thủ SLA (0-100) |
| `metrics.avg_delivery_hours` | number | Thời gian giao hàng trung bình (giờ) |

### Error Responses

**400 Bad Request:**
```json
{
  "detail": "Invalid date format. Use YYYY-MM-DD"
}
```

**403 Forbidden:**
```json
{
  "detail": "Only Admin or Operator can access SLA report"
}
```

### SLA Metrics Explanation

- **SLA Compliance:** Phần trăm đơn hàng giao trong vòng 24 giờ kể từ lúc tạo đơn
- **On-Time Orders:** Số đơn hàng có thời gian giao ≤ 24 giờ
- **Average Delivery Hours:** Trung bình cộng thời gian giao của tất cả đơn hàng hoàn thành
- **Completed Orders:** Chỉ tính đơn hàng có trạng thái `DELIVERED_TO_HUB` (đã giao khách)

### Ví dụ cURL

```bash
curl -X GET "http://localhost:8000/api/v1/missions/audit/sla-report?start_date=2026-10-01&end_date=2026-10-31" \
  -H "Authorization: Bearer admin_token_xyz"
```

---

## 🔐 Access Control

Cả hai endpoint đều yêu cầu role **Admin** hoặc **Operator**. Technician không có quyền truy cập.

```python
# Chỉ Admin/Operator mới có quyền
@missions_router.get("/{mission_id}/audit-trail")
async def get_order_audit_trail(
    user: CurrentUser = Depends(allow_admin_or_operator),  # ← Here
    ...
):
```

---

## 🎯 Frontend Implementation Guide

### 1. Audit Trail Page

**Layout:**
```
┌─────────────────────────────────────────┐
│ Audit Trail - Mission XYZ               │
├─────────────────────────────────────────┤
│ Mission ID: mission_123                 │
│ Order ID: order_456                     │
│ Drone: drone_DJI_001                    │
├─────────────────────────────────────────┤
│ Timeline (vertical):                    │
│ ✓ 08:30 - Order Created                 │
│ ✓ 08:35 - Payment Confirmed             │
│ ✓ 09:00 - Package at Hub (tech_user_001)│
│ ✓ 09:15 - Drone Dispatched              │
│ ✓ 09:20 - Leg 1 Started (HN→Relay)      │
│ ✓ 09:35 - Leg 1 Completed               │
│ ✓ 09:40 - Leg 2 Started (Relay→HCM)     │
│ ✓ 10:00 - Leg 2 Completed               │
│ ✓ 10:05 - Drone Arrived                 │
│ ✓ 10:15 - Customer Confirmed Pickup     │
├─────────────────────────────────────────┤
│ Summary:                                │
│ Total Duration: 29.5 hours              │
│ Total Events: 11                        │
└─────────────────────────────────────────┘
```

**React Component Example:**
```jsx
const AuditTrailPage = ({ missionId }) => {
  const [audit, setAudit] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/v1/missions/${missionId}/audit-trail`, {
      headers: { "Authorization": `Bearer ${token}` }
    })
      .then(res => res.json())
      .then(data => {
        setAudit(data);
        setLoading(false);
      });
  }, [missionId]);

  if (loading) return <div>Loading...</div>;

  return (
    <div className="audit-trail">
      <h1>Audit Trail</h1>
      <div className="info">
        <p>Mission: {audit.mission_id}</p>
        <p>Order: {audit.order_id}</p>
        <p>Drone: {audit.drone_id}</p>
      </div>
      
      <div className="timeline">
        {audit.timeline.map((event, idx) => (
          <div key={idx} className="timeline-event">
            <div className="timestamp">
              {new Date(event.timestamp).toLocaleString()}
            </div>
            <div className="event-name">{event.event}</div>
            <div className="actor">By: {event.actor}</div>
            {event.hub_id && <div className="detail">Hub: {event.hub_id}</div>}
            {event.issue && <div className="detail">{event.issue}</div>}
          </div>
        ))}
      </div>

      <div className="summary">
        <h3>Summary</h3>
        <p>Total Events: {audit.summary.total_events}</p>
        <p>Duration: {audit.summary.total_duration} hours</p>
        <p>From: {new Date(audit.summary.first_event).toLocaleString()}</p>
        <p>To: {new Date(audit.summary.last_event).toLocaleString()}</p>
      </div>
    </div>
  );
};
```

### 2. SLA Report Page

**Layout:**
```
┌──────────────────────────────────────────────┐
│ SLA Report                                   │
├──────────────────────────────────────────────┤
│ Period: 2026-10-01 to 2026-10-31             │
├──────────────────────────────────────────────┤
│ Metrics:                                     │
│ ┌─ Total Orders: 150                      ─┐│
│ │ ✓ Completed: 145                        │
│ │ ✗ Cancelled: 5                          │
│ │ ✓ On-Time: 142 / 150                    │
│ └─ SLA Compliance: 94.67%                 ─┘
│                                              │
│ Avg Delivery Time: 18.5 hours                │
│                                              │
│ ┌─ Status Bars ─────────────────────────┐  │
│ │ ████████████████░░░ 94.67% (142/150)  │  │
│ └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

**React Component Example:**
```jsx
const SLAReportPage = () => {
  const [report, setReport] = useState(null);
  const [startDate, setStartDate] = useState("2026-10-01");
  const [endDate, setEndDate] = useState("2026-10-31");

  const handleFetch = async () => {
    const res = await fetch(
      `/api/v1/missions/audit/sla-report?start_date=${startDate}&end_date=${endDate}`,
      { headers: { "Authorization": `Bearer ${token}` } }
    );
    const data = await res.json();
    setReport(data);
  };

  return (
    <div className="sla-report">
      <h1>SLA Report</h1>
      
      <div className="filters">
        <input 
          type="date" 
          value={startDate} 
          onChange={(e) => setStartDate(e.target.value)}
        />
        <input 
          type="date" 
          value={endDate} 
          onChange={(e) => setEndDate(e.target.value)}
        />
        <button onClick={handleFetch}>Generate Report</button>
      </div>

      {report && (
        <div className="results">
          <p>Period: {report.period}</p>
          
          <div className="metrics">
            <div className="metric-card">
              <h3>Total Orders</h3>
              <p className="big-number">{report.metrics.total_orders}</p>
            </div>
            
            <div className="metric-card">
              <h3>Completed</h3>
              <p className="big-number">{report.metrics.completed_orders}</p>
            </div>
            
            <div className="metric-card">
              <h3>SLA Compliance</h3>
              <p className="big-number">{report.metrics.sla_compliance_percent}%</p>
              <div className="progress-bar">
                <div 
                  className="progress-fill" 
                  style={{ width: `${report.metrics.sla_compliance_percent}%` }}
                />
              </div>
            </div>
            
            <div className="metric-card">
              <h3>Avg Delivery Time</h3>
              <p className="big-number">{report.metrics.avg_delivery_hours}h</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
```

---

## 📊 Use Cases & Scenarios

### Scenario 1: Khách hàng khiếu nại thời gian giao hàng
**Quy trình:**
1. Admin mở Audit Trail của mission đó
2. Xem timeline chi tiết từ lúc đặt hàng đến giao nhận
3. So sánh với SLA (24 giờ)
4. Nếu vượt quá → có bằng chứng để phân tích lý do (maintenance, relay delays, etc.)

### Scenario 2: Báo cáo hiệu suất hàng tháng
**Quy trình:**
1. Operator truy cập SLA Report
2. Chọn khoảng thời gian (tháng)
3. Xem compliance %, số đơn đúng hạn
4. Export báo cáo cho leadership

### Scenario 3: Kiểm toán nội bộ
**Quy trình:**
1. Internal Auditor truy cập Audit Trail của mission chứa hiện vật bất thường
2. Xem lịch sử bảo trì, kiểm tra xem có maintenance nào không được ghi chép
3. Xác minh dữ liệu tuân thủ quy định

---

## ⚠️ Notes for Frontend

- **Timestamp Format:** Tất cả timestamp đều ở ISO 8601 format (UTC). Frontend nên convert sang local timezone của user
- **Date Input:** SLA Report dùng `start_date` & `end_date` ở format **YYYY-MM-DD**
- **Timeline Sorting:** Timeline từ backend đã được sort theo thời gian (asc), không cần sort lại
- **Error Handling:** Nếu mission không tồn tại → 404. Nếu user không phải Admin/Operator → 403
- **Performance:** Audit Trail có thể chứa hàng chục events → cân nhắc pagination nếu cần

---

## 🔗 Related Endpoints

- [Flight History](./FLIGHT_HISTORY_API.md) - Xem lịch bay của drone
- [Maintenance History](./MAINTENANCE_HISTORY_API.md) - Xem lịch bảo trì drone
- [Live Tracking API](./LIVE_TRACKING_API.md) - Theo dõi drone real-time

---

**Last Updated:** October 18, 2026  
**Version:** 1.0
