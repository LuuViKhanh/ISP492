# Tóm tắt trạng thái & nhiệm vụ

---

## 1. Mission.status — Vòng đời mission

| Status | Ý nghĩa | Ai quan tâm |
|---|---|---|
| `Scheduled` | Operator đã tạo mission, chờ Technician chuẩn bị | Technician |
| `Flying` | Drone đang bay | Operator (live tracking) |
| `Incident return` | Drone quay về do sự cố | Technician (xử lý sự cố) |
| `Completed` | Drone đáp hub đích, chờ Technician confirm | Technician |
| `Mission completed` | Mission hoàn tất hoàn toàn | Lịch sử |
| `Cancelled` | Hủy bỏ | Lịch sử |
| `Failed` | Thất bại | Lịch sử |

---

## 2. Mission.handling_status — Trạng thái xử lý tại hub

| Handling Status | Ý nghĩa | Technician làm gì |
|---|---|---|
| `At hub` | Drone đang ở hub, chờ xử lý | Lắp pin → bấm `/ready` |
| `Ready` | Đã lắp pin, sẵn sàng bay | Bấm `/dispatch` |
| `Incoming` | Drone đang bay, chưa đến hub | Chờ, chuẩn bị pin |
| `Cannot continue` | Drone về do incident | Confirm arrival + tạo Work Order |

---

## 3. MissionLeg.status — Trạng thái từng chặng bay

| Leg Status | Ý nghĩa |
|---|---|
| `PENDING` | Chặng chưa đến lượt bay |
| `IN_PROGRESS` | Drone đang bay chặng này |
| `COMPLETED` | Chặng đã hoàn thành |

---

## 4. Flow đầy đủ

```
[Origin Hub]
  mission.status = Scheduled
  mission.handling_status = At hub
      ↓ Technician lắp pin
  POST /missions/{id}/ready
      → handling_status = Ready
      ↓ Technician bấm Dispatch
  POST /missions/{id}/dispatch
      → status = Flying
      → handling_status = Incoming
      → drone.status = In Flight, drone.current_hub_id = null
      → battery.current_hub_id = null

            ↓ [Đang bay Leg 1: Hub A → Hub B]
            MissionLeg 1: IN_PROGRESS
            Technician Hub B thấy trong /incoming

      [Relay Hub B - nếu có]
      Drone đáp Hub B
      POST /missions/{id}/confirm-relay-arrival
          → handling_status = At hub
          → drone.current_hub_id = Hub B
          → pin cũ: drone_id = null, về Battery Management Hub B

      POST /missions/{id}/swap-battery
          → pin cũ tháo ra, pin mới lắp vào
          → handling_status = Ready

      POST /missions/{id}/dispatch
          → status = Flying
          → handling_status = Incoming
          → MissionLeg 2: IN_PROGRESS
          → Technician Hub C thấy trong /incoming

            ↓ [Đang bay Leg 2: Hub B → Hub C]

      [Hub đích C]
      Drone đáp Hub C
      mission.status = Completed
      mission.handling_status = At hub

      POST /missions/{id}/confirm-arrival
          → status = Mission completed / Completed
          → handling_status = null (rời /incoming, vào /history)
          → drone.current_hub_id = Hub C, status = Available
          → pin: drone_id = null, về Battery Management Hub C
          → order.status = DELIVERED_TO_HUB
```

---

## 5. Technician thấy gì trong /incoming

| Điều kiện | Lý do hiển thị |
|---|---|
| `Drone.current_hub_id == tech_hub_id` | Drone đang ở hub của technician |
| `Mission.destination_hub_id == tech_hub_id` | Mission bay về hub này |
| `MissionLeg IN_PROGRESS + to_hub_id == tech_hub_id` | Chặng đang bay **đến** hub này |

> **Lưu ý:** Technician chỉ thấy drone đang bay **về** hub mình, không thấy drone đang bay đi từ hub mình.

---

## 6. Các API liên quan (Technician)

| API | Điều kiện | Kết quả |
|---|---|---|
| `POST /missions/{id}/ready` | `handling_status = At hub` | → `Ready` |
| `POST /missions/{id}/dispatch` | `handling_status = Ready` | → `Flying + Incoming` |
| `POST /missions/{id}/confirm-relay-arrival` | Drone đáp relay hub | → `At hub`, pin tháo ra |
| `POST /missions/{id}/swap-battery` | `handling_status = At hub` | → `Ready` với pin mới |
| `POST /missions/{id}/confirm-arrival` | Drone đáp hub đích | → Mission hoàn tất |
