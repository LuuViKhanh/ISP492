# Cấu trúc Cơ sở dữ liệu - DroneOptAI (FA26IS01)

Tài liệu này định nghĩa cấu trúc cơ sở dữ liệu và các luồng tương tác chuẩn xác theo yêu cầu thiết kế của hệ thống **Energy-Efficient Drone Delivery System**, bao gồm yêu cầu của **FE Operator (Spec VI)** và phân hệ Technician (Maintenance, Fleet & Work Orders).

---

## I. Cấu trúc các bảng (Tables)

### A. Nhóm Quản lý Người dùng & Phân quyền (Auth & Users)

**1. Bảng `roles`** (Lưu trữ các nhóm quyền)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | ID quyền |
| `role_name` | VARCHAR(50) | Unique, Not Null | Tên quyền (Admin, Operator, Technician, Customer) |
| `description` | VARCHAR(255) | Nullable | Mô tả chi tiết |

**2. Bảng `hubs`** (Quản lý các trạm bay/điểm kỹ thuật)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT / UUID | **PK** | ID trạm |
| `code` | VARCHAR(30) | Unique | Mã trạm |
| `name` | VARCHAR(100) | | Tên trạm |
| `latitude` | DECIMAL(9,6) | | Vĩ độ |
| `longitude`| DECIMAL(9,6) | | Kinh độ |

**3. Bảng `users`** (Lưu trữ tài khoản)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | ID người dùng |
| `role_id` | VARCHAR(50) | **FK** -> `roles.id` | Quyền của user |
| `hub_id` | INT / UUID | **FK** -> `hubs.id`, Nullable | Trạm làm việc (dành cho Technician) |
| `username` | VARCHAR(50) | Unique, Not Null | Tên đăng nhập |
| `password_hash` | VARCHAR(255) | Not Null | Mật khẩu đã mã hóa |
| `full_name` | VARCHAR(100) | Not Null | Họ và tên |

### B. Nhóm Quản lý Đội bay & Thiết bị (Fleet Management)

**4. Bảng `drones`** (Thông tin máy bay)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID máy bay |
| `name` | VARCHAR(100) | Not Null | Tên máy bay |
| `model` | VARCHAR(100) | Not Null | Đời máy |
| `current_hub_id`| INT / UUID | **FK** -> `hubs.id`, Nullable | Hiện đang nằm tại Hub nào |
| `operational_status`| VARCHAR(50) | Enum | `AVAILABLE`, `MAINTENANCE`, `IN_MISSION` |

**5. Bảng `batteries`**
*(Lưu kho pin, % pin, trạng thái thay thế, v.v.)*

### C. Nhóm Đơn hàng & Chuyến bay (Order & Mission - Tương thích Spec VI)

**6. Bảng `orders`** (Đơn hàng từ khách hàng)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | Mã đơn (VD: ORD-1114) |
| `customer_id` | VARCHAR(50) | **FK** -> `users.id` | Khách hàng |
| `package_label` | VARCHAR(255) | | Tên kiện hàng |
| `payload_kg` | DECIMAL(5,2) | | Khối lượng |
| `origin_hub_id` | INT | **FK** -> `hubs.id` | Trạm gửi hàng |
| `destination_hub_id` | INT | **FK** -> `hubs.id` | Trạm nhận hàng |
| `delivery_mode` | VARCHAR(50) | Enum | `EXPRESS`, `SCHEDULED` |
| `status` | VARCHAR(50) | Enum | `PENDING`, `IN_DELIVERY`, `DELIVERED_TO_HUB`, `CANCELLED` |
| `origin_received_at` | TIMESTAMP | | Hub gửi đã nhận kiện hàng |
| `destination_received_at`| TIMESTAMP | | Hub nhận đã nhận kiện hàng |
| `planning_at` | TIMESTAMP | | Thời điểm hệ thống cho phép tạo Mission |

**7. Bảng `missions`** (Chuyến bay do Operator lên lịch)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT8 | Primary, Identity, Non-nullable |  |
| `customer_id` | VARCHAR | Non-nullable |  |
| `operator_id` | VARCHAR | Nullable |  |
| `drone_id` | INT8 | Foreign key, Nullable |  |
| `battery_id` | INT8 | Foreign key, Nullable |  |
| `pickup_location_id` | INT4 | Foreign key, Nullable |  |
| `dropoff_location_id` | INT4 | Foreign key, Nullable |  |
| `payload_weight` | FLOAT8 | Non-nullable |  |
| `distance_m` | FLOAT8 | Non-nullable | Khoảng cách tuyến đường (mét) |
| `delivery_fee` | FLOAT8 | Nullable | Phí giao hàng tính toán cho đơn này |
| `status` | MISSIONS_STATUS | Non-nullable |  |
| `scheduled_time` | TIMESTAMP | Non-nullable | Thời gian bay dự kiến |
| `start_time` | TIMESTAMP | Nullable | Thời gian cất cánh thực tế |
| `end_time` | TIMESTAMP | Nullable | Thời gian hạ cánh thực tế |
| `origin_hub_id` | INT4 | Nullable |  |
| `destination_hub_id` | INT4 | Nullable |  |
| `departed_at` | TIMESTAMP | Nullable |  |
| `arrived_at` | TIMESTAMP | Nullable |  |
| `arrival_confirmed_by` | VARCHAR | Nullable |  |
| `arrival_confirmed_at` | TIMESTAMP | Nullable |  |
| `approval_deadline` | TIMESTAMP | Nullable | Hỗ trợ dữ liệu cho API trả về làm hiệu ứng đếm ngược trên UI và làm mốc thời gian cho Worker tự động hủy đơn |
| `handling_status` | MISSIONS_HANDLING_STATUS | Nullable |  |
| `order_code` | VARCHAR | Nullable |  |
| `mission_code` | VARCHAR | Nullable |  |
| `order_id` | VARCHAR | Nullable |  |
| `route_id` | VARCHAR | Nullable |  |
| `planned_start_at` | TIMESTAMP | Nullable |  |
| `package_receive_deadline_at` | TIMESTAMP | Nullable |  |
| `estimated_arrival_at` | TIMESTAMP | Nullable |  |
| `actual_started_at` | TIMESTAMP | Nullable |  |
| `actual_completed_at` | TIMESTAMP | Nullable |  |
| `predicted_duration_min` | INT4 | Nullable |  |
| `estimated_energy_wh` | NUMERIC | Nullable |  |
| `battery_consumption_pct` | INT4 | Nullable |  |
| `predicted_remaining_battery_pct` | INT4 | Nullable |  |
| `confidence_pct` | INT4 | Nullable |  |
| `risk_level` | VARCHAR | Nullable |  |
| `created_by` | VARCHAR | Nullable |  |
| `created_at` | TIMESTAMP | Nullable |  |
| `updated_at` | TIMESTAMP | Nullable |  |
| `cancel_reason` | TEXT | Nullable |  |
| `failure_reason` | TEXT | Nullable |  |

**8. Bảng `mission_legs`** (Lộ trình qua các Hub trung chuyển)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK** | |
| `mission_id` | INT | **FK** -> `missions.id` | |
| `sequence_no` | INT | | Thứ tự chặng bay (1, 2, 3...) |
| `from_hub_id` | INT | **FK** -> `hubs.id` | Hub xuất phát của chặng |
| `to_hub_id` | INT | **FK** -> `hubs.id` | Hub đích của chặng |
| `status` | VARCHAR(50) | Enum | `PENDING`, `IN_PROGRESS`, `COMPLETED` |

**9. Bảng `mission_drone_assignment_history`** (Lịch sử đổi Drone)
Dùng để theo dõi việc Replace Drone khi có sự cố hoặc drone ban đầu không đảm bảo.

### D. Nhóm Sự cố & Bảo trì (Maintenance & Incidents)

**10. Bảng `incidents`**
*(Chứa thông tin sự cố, `mission_id`, `drone_id`, `severity`, `status`)*

**11. Bảng `maintenance_alerts` & `work_orders`**
*(Chứa dữ liệu cảnh báo bảo trì và lệnh thực hiện công việc kỹ thuật cho Technician)*

---

## II. Luồng nghiệp vụ Operator (Order-driven)

1. **Khách hàng tạo Order:** Hệ thống lưu vào bảng `orders` với trạng thái `PENDING`. Dựa vào `delivery_mode`, tính ra `planning_at` (thời điểm được lên lịch).
2. **Operator Planning:**
   - Lấy danh sách Order `PENDING` và đã tới giờ `planning_at`.
   - Tìm kiếm Drone hợp lệ (ở `origin_hub_id`, trạng thái `AVAILABLE`, pin đủ).
   - Gọi AI dự đoán Route (`mission_legs`) và năng lượng (`estimated_energy_wh`).
   - Operator tạo Mission: Hệ thống insert vào `missions`, và tạo các `mission_legs`. Trạng thái Mission là `SCHEDULED`. Order vẫn giữ là `PENDING`.
3. **Thực thi Mission (Final Validation):**
   - Chỉ khi Hub báo đã nhận kiện hàng (`origin_received_at` có giá trị), Mission mới bắt đầu bay -> Mission chuyển `IN_PROGRESS`.
   - Order chuyển sang `IN_DELIVERY`.
4. **Theo dõi đa chặng (Relay Hubs):**
   - Drone bay qua các chặng, `mission_legs` lần lượt chuyển từ `PENDING` -> `IN_PROGRESS` -> `COMPLETED`.
5. **Hoàn thành:**
   - Khi hạ cánh an toàn tại `destination_hub_id` và khách xác nhận hoặc Hub nhận kiện hàng, Order chuyển thành `DELIVERED_TO_HUB`, Mission chuyển `COMPLETED`.
