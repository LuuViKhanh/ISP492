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
| `id` | INT / BIGINT | **PK** | ID nội bộ chuyến bay |
| `order_id` | VARCHAR(50) | **FK** -> `orders.id` | Đơn hàng gốc |
| `drone_id` | INT | **FK** -> `drones.id` | Máy bay phân công |
| `route_id` | VARCHAR(50) | | ID lộ trình đã được AI chọn |
| `status` | VARCHAR(50) | Enum | `SCHEDULED`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `FAILED` |
| `origin_hub_id` | INT | **FK** -> `hubs.id` | |
| `destination_hub_id` | INT | **FK** -> `hubs.id` | |
| `predicted_duration_min`| INT | | Thời gian bay dự kiến |
| `estimated_energy_wh` | DECIMAL(6,2)| | Dự báo năng lượng |
| `battery_consumption_pct`| INT | | Dự báo % pin tiêu thụ |

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
