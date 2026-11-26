# Tóm tắt trạng thái & nhiệm vụ

---

## 1. Mission.status — Vòng đời mission

| Enum Value | Display Text | Ý nghĩa | Khi nào được set |
|---|---|---|---|
| `SCHEDULED` | Scheduled | Operator đã tạo mission, chờ Technician chuẩn bị | Lúc create mission (operator) |
| `FLYING` | Flying | Drone đang bay | Dispatcher gọi / auto-normalize từ SCHEDULED |
| `ARRIVAL_COMPLETED` | Arrival completed | Drone đáp hub (relay hoặc đích) | After confirm-relay-arrival hoặc confirm-arrival |
| `MISSION_COMPLETED` | Mission completed | Mission hoàn tất hoàn toàn | After confirm-arrival (clean delivery) |
| `INCIDENT_RETURN` | Incident return | Drone quay về do sự cố | Simulator phát hiện incident, Technician confirm |
| `FAILED` | Failed | Mission thất bại (crash/lost) | Simulator phát hiện crash |
| `CANCELLED` | Cancelled | Mission bị hủy (manual hoặc overdue) | Manual cancel hoặc auto-cancel (>3 ngày overdue) |

---

## 2. Mission.handling_status — Trạng thái xử lý tại hub (chỉ khi mission nằm trong `/incoming`)

| Enum Value | Display Text | Ý nghĩa | Technician làm gì |
|---|---|---|---|
| `Incoming` | Incoming | Drone đang bay, chưa đến hub | Chờ, chuẩn bị pin |
| `At hub` | At hub | Drone đang ở hub (confirm relay/arrival), chờ xử lý | Nếu relay: swap pin; Nếu đích: confirm-pickup |
| `Ready` | Ready | Đã lắp pin xong, sẵn sàng bay chặng tiếp theo | Bấm `/dispatch` để cất cánh |
| `Cannot continue` | Cannot continue | Drone về do incident, không thể tiếp tục | Confirm arrival + tạo Work Order, xử lý sự cố |

**Lưu ý:** `handling_status` chỉ có giá trị khi mission **đang nằm trong `/incoming`** (drone chưa hoàn tất giao đơn). Khi mission hoàn tất (`ARRIVAL_COMPLETED`, `MISSION_COMPLETED`, `INCIDENT_RETURN`, `FAILED`), `handling_status` sẽ được set về `NULL`.

---

## 3. MissionLeg.status — Trạng thái từng chặng bay

| Leg Status | Ý nghĩa |
|---|---|
| `PENDING` | Chặng chưa đến lượt bay |
| `IN_PROGRESS` | Drone đang bay chặng này |
| `COMPLETED` | Chặng đã hoàn thành |

---

## 4. Flow đầy đủ (sử dụng status thực tế)

```
[Origin Hub - Technician]
  Operator tạo & gọi Assign mission
  → mission.status = SCHEDULED
  → mission.handling_status = AT_HUB
  
  Technician lắp pin, kiểm tra drone
  POST /missions/{id}/ready
  → mission.status = SCHEDULED
  → mission.handling_status = READY
  
  Technician bấm Dispatch
  POST /missions/{id}/dispatch
  → mission.status = FLYING (auto-normalize từ SCHEDULED)
  → mission.handling_status = INCOMING
  → drone.status = IN_FLIGHT
  → drone.current_hub_id = NULL
  → battery.current_hub_id = NULL
  → MissionLeg 1: IN_PROGRESS

        ↓ [Đang bay Leg 1: Hub A → Hub B]
        Technician Hub B thấy trong /incoming

  [Relay Hub B - nếu có]
  Drone đáp Hub B (simulator phát hiện)
  → mission.status = ARRIVAL_COMPLETED
  → mission.handling_status = AT_HUB
  → drone.current_hub_id = Hub B
  → battery cũ: drone_id = NULL, back to Battery Management
  
  POST /missions/{id}/swap-battery (Technician)
  → Nếu operator đã chỉ định pin: auto-select
  → Nếu không: auto-select pin năng lượng cao nhất
  → mission.handling_status = READY
  → MissionLeg 2 battery được update
  
  POST /missions/{id}/dispatch (Technician)
  → mission.status = FLYING
  → mission.handling_status = INCOMING
  → MissionLeg 2: IN_PROGRESS
  → Technician Hub C thấy trong /incoming

        ↓ [Đang bay Leg 2: Hub B → Hub C]

  [Hub đích C - Technician]
  Drone đáp Hub C (simulator phát hiện)
  → mission.status = ARRIVAL_COMPLETED
  → mission.handling_status = AT_HUB
  → drone.current_hub_id = Hub C
  
  POST /missions/{id}/confirm-arrival (Technician)
  → Nếu clean delivery: mission.status = MISSION_COMPLETED
  → Nếu có incident: mission.status = ARRIVAL_COMPLETED (đã xác nhận tại đích)
  → mission.handling_status = NULL (rời khỏi /incoming → vào /history)
  → drone.current_hub_id = Hub C, status = AVAILABLE
  → order.status = DELIVERED_TO_HUB

[Trường hợp sự cố]
  Drone gặp sự cố (simulator/failsafe phát hiện)
  → mission.status = INCIDENT_RETURN (hoặc FAILED nếu crash)
  → mission.handling_status = INCOMING
  → mission.destination_hub_id = nearest_hub (auto select)
  
  Drone quay về hub gần nhất
  → mission.status = INCIDENT_RETURN
  → mission.handling_status = CANNOT_CONTINUE
  → Technician confirm arrival + create Work Order
  → mission.handling_status = NULL

[Trường hợp hủy]
  Manual cancel hoặc auto-cancel (>3 ngày chờ Technician pickup)
  → mission.status = CANCELLED
  → mission.handling_status = NULL (nếu còn)
```

---

## 5. Mission nào hiển thị trong `/incoming` (Technician)

Mission sẽ hiển thị trong `/incoming` **khi và chỉ khi:**

| Điều kiện | Chi tiết |
|---|---|
| `handling_status != NULL` | Phải là `INCOMING`, `AT_HUB`, `READY`, hoặc `CANNOT_CONTINUE` |
| `Drone.current_hub_id == tech_hub_id` HOẶC `MissionLeg[PENDING].to_hub_id == tech_hub_id` | Drone đang ở/sắp đến hub của technician |

**Cụ thể:**
- **INCOMING**: Drone đang bay đến hub → hiển thị trong 20-30 phút trước khi drone tới (chuẩn bị pin)
- **AT_HUB**: Drone đáp hub, chờ xử lý (relay: swap pin; đích: confirm-pickup)
- **READY**: Pin đã sẵn sàng, chờ dispatcher
- **CANNOT_CONTINUE**: Drone về do sự cố, chờ Technician xử lý

**Khi nào rời `/incoming`:**
- Khi `handling_status = NULL` (mission hoàn tất hoặc bị hủy)
- Chuyển sang `/history` (Completed, Cancelled, Failed, etc.)

---

## 6. Các API liên quan (Technician)

| API | Điều kiện | Kết quả |
|---|---|---|
| `POST /missions/{id}/ready` | `handling_status = At hub` | → `Ready` |
| `POST /missions/{id}/dispatch` | `handling_status = Ready` | → `Flying + Incoming` |
| `POST /missions/{id}/confirm-relay-arrival` | Drone đáp relay hub | → `At hub`, pin tháo ra |
| `POST /missions/{id}/swap-battery` | `handling_status = At hub` | → `Ready` với pin mới |
| `POST /missions/{id}/confirm-arrival` | Drone đáp hub đích | → Mission hoàn tất |
