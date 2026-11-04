# Dashboard — Công thức tính toán các chỉ số

> Tài liệu này mô tả chính xác cách từng số liệu trên Dashboard được tính.  
> Nguồn dữ liệu: PostgreSQL (Supabase), không có module Payment riêng.

---

## 1. Admin Dashboard

### 1.1 Revenue (Doanh thu)

| Chỉ số | Công thức |
|---|---|
| `total` | `SUM(missions.delivery_fee)` với `status IN ('Completed', 'Mission completed')` |
| `today` | Tương tự nhưng lọc `arrival_confirmed_at >= today_start` (00:00 hôm nay) |
| `this_month` | Tương tự nhưng lọc `>= first_day_of_month` |
| `paid_orders` | `COUNT(*)` missions đã Completed |
| `pending_payments` | Luôn = 0 (không có module Payment) |
| `refunds` | Luôn = 0 |

> **Lưu ý timestamp**: dùng `COALESCE(arrival_confirmed_at, end_time, departed_at)` để lấy thời điểm hoàn tất.

---

### 1.2 Users

| Chỉ số | Công thức |
|---|---|
| `total` | `COUNT(*)` bảng `users` |
| `by_role` | `GROUP BY role_id` → map sang tên role (ADMIN/OPERATOR/TECHNICIAN/CUSTOMER) |
| `active` | `COUNT(*) WHERE is_active = TRUE` |
| `inactive` | `total - active` |

---

### 1.3 Missions

| Chỉ số | Công thức |
|---|---|
| `total` | `COUNT(*)` bảng `missions` |
| `by_status` | `GROUP BY status` |
| `success_rate` | `completed / (completed + cancelled + rejected + failed) × 100` |

> Trạng thái "completed": `status IN ('Completed', 'Mission completed')`  
> Trạng thái "terminal": completed + cancelled + rejected + failed

---

### 1.4 Fleet

| Chỉ số | Công thức |
|---|---|
| `total_drones` | `COUNT(*)` bảng `drones` |
| `by_status` | `GROUP BY operational_status` |
| `avg_utilization_pct` | `AVG(drones.utilization_pct)` |

---

### 1.5 Batteries

| Chỉ số | Công thức |
|---|---|
| `total` | `COUNT(*)` bảng `batteries` |
| `by_status` | `GROUP BY status` |
| `avg_charge_pct` | `AVG(batteries.charge_level_pct)` |

---

### 1.6 AI Predictions

| Chỉ số | Công thức |
|---|---|
| `missions_with_prediction` | `COUNT(DISTINCT mission_id)` từ `ai_predictions WHERE estimated_energy_wh IS NOT NULL` |
| `avg_estimated_energy_wh` | `AVG(ai_predictions.estimated_energy_wh)` |
| `risk_breakdown` | `GROUP BY ai_predictions.risk_level` |

---

### 1.7 Alerts

| Chỉ số | Công thức |
|---|---|
| `pending_maintenance_alerts` | `COUNT(*) FROM maintenance_alerts WHERE status = 'Pending'` |
| `open_incidents` | `COUNT(*) FROM incidents WHERE status = 'Open'` |
| `total_audit_logs` | `COUNT(*) FROM audit_logs` |

---

## 2. Operator Dashboard

### 2.1 Mission KPI

| Chỉ số | Công thức |
|---|---|
| `total` | `COUNT(*)` tất cả missions |
| `completed` | `status IN ('Completed', 'Mission completed')` |
| `cancelled` | `status = 'Cancelled'` |
| `rejected` | `status = 'Rejected'` |
| `success_rate` | `completed / terminal × 100` (terminal = completed + cancelled + rejected + failed) |
| `avg_distance_m` | `AVG(missions.distance_m)` |
| `avg_payload_kg` | `AVG(missions.payload_weight)` |
| `total_revenue` | `SUM(missions.delivery_fee)` với completed missions |

---

### 2.2 Energy KPI

| Chỉ số | Công thức |
|---|---|
| `total_estimated_energy_wh` | `SUM(ai_predictions.estimated_energy_wh)` — lấy prediction mới nhất/mission |
| `avg_estimated_energy_wh` | `AVG(ai_predictions.estimated_energy_wh)` |
| `wh_per_km` | `total_energy_wh / total_distance_km` |

---

### 2.3 Route Efficiency

| Chỉ số | Công thức |
|---|---|
| `total_distance_m` | `SUM(missions.distance_m)` |
| `avg_distance_m` | `AVG(missions.distance_m)` |
| `avg_predicted_duration_min` | `AVG(ai_predictions.estimated_duration_m)` |

---

### 2.4 Alerts

| Chỉ số | Công thức |
|---|---|
| `pending_maintenance_alerts` | `COUNT(*) WHERE maintenance_alerts.status = 'Pending'` |
| `open_incidents` | `COUNT(*) WHERE incidents.status = 'Open'` |
| `overdue_approvals` | `COUNT(*) WHERE status = 'Pending Approval' AND scheduled_time <= now()` |

---

## 3. Technician Dashboard

| Chỉ số | Công thức |
|---|---|
| `fleet_status` | `GROUP BY drones.operational_status` |
| `total_drones` | `COUNT(*)` drones |
| `available_drones` | `COUNT(*) WHERE operational_status = 'Available'` |
| `drone_availability_pct` | `available / total × 100` |
| `avg_utilization_pct` | `AVG(drones.utilization_pct)` |
| `battery_status` | `GROUP BY batteries.status` |
| `total_batteries` | `COUNT(*)` batteries |
| `avg_battery_charge_pct` | `AVG(batteries.charge_level_pct)` |
| `low_battery_count` | `COUNT(*) WHERE charge_level_pct < 20` |
| `pending_maintenance_alerts` | `COUNT(*) WHERE status = 'Pending'` (filter theo hub nếu Technician) |

---

## 4. Customer Dashboard

| Chỉ số | Công thức |
|---|---|
| `active_deliveries` | Đơn có mission status `IN_PROGRESS / FLYING / ACTIVE_MISSION / SCHEDULED` |
| `completed_deliveries` | Mission `COMPLETED / MISSION_COMPLETED` |
| `cancelled_deliveries` | Order `status = 'CANCELLED'` |
| `total_deliveries` | `COUNT(*)` orders của customer |
| `total_spent` | `SUM(orders.delivery_fee)` với `payment_status = 'PAID'` |
| `avg_payload_kg` | `AVG(orders.payload_kg)` |
| `total_distance_m` | `SUM(missions.distance_m)` của các missions hoàn tất |
| `status_breakdown` | `GROUP BY order.status` |

---

## 5. Priority Scoring (Order Planning)

Áp dụng khi Operator xem danh sách đơn chờ lập lịch:

```
PriorityScore = BasePriority + UrgencyScore

BasePriority:
  EXPRESS   = 60
  SCHEDULED = 35
  NORMAL    = 10

coordination_deadline = estimated_window_end - (flight_mins + 30 phút)
Slack = coordination_deadline - now()  [phút]

UrgencyScore = min(100, max(0, 100 × (60 - Slack) / 60))

priority_level:
  CRITICAL : Slack ≤ 0   (đã quá hạn điều phối)
  HIGH     : Slack ≤ 30
  MEDIUM   : Slack ≤ 60
  LOW      : Slack > 60
```

---

## 6. Incident Compensation (Đền bù sự cố)

| Trường | Mô tả |
|---|---|
| `incidents.compensation_pct` | % giá trị đơn được đền bù (0–100) |
| `incidents.compensation_note` | Ghi chú chi tiết lý do, số tiền, ngày xác nhận |

**Case demo**: Drone mất tín hiệu, mất hàng → đền bù **70%**  
Công thức: `compensation_amount = orders.delivery_fee × compensation_pct / 100`  
Ví dụ: `195,000 × 70% = 136,500 VNĐ`

Miễn 30% áp dụng theo điều khoản 8.3: sự cố do thời tiết ngoài tầm kiểm soát (Beaufort 7+).
