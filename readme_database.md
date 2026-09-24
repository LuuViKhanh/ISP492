# Cấu trúc Cơ sở dữ liệu - DroneOptAI (FA26IS01)

Tài liệu này định nghĩa cấu trúc cơ sở dữ liệu và các luồng tương tác chuẩn xác theo yêu cầu thiết kế của hệ thống **Energy-Efficient Drone Delivery System**. Cấu trúc này bao gồm đầy đủ các tính năng Trí tuệ nhân tạo (AI), Vận hành (Operations), Quản trị dữ liệu (Data Governance), phân hệ Thanh toán (Payment & Revenue) và đã được **cải tiến thêm phân hệ Technician (Maintenance, Fleet & Work Orders)** mà không phá vỡ cấu trúc cũ.

---

## I. Cấu trúc các bảng (Tables)

### A. Nhóm Quản lý Người dùng & Phân quyền (Auth & Users)

**1. Bảng `roles`** (Lưu trữ các nhóm quyền)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | ID quyền |
| `role_name` | VARCHAR(50) | Unique, Not Null | Tên quyền (Admin, Operator, Technician, Customer) |
| `description` | VARCHAR(255) | Nullable | Mô tả chi tiết |

**2. Bảng `hubs`** (BẢNG MỚI - Quản lý các trạm bay/điểm kỹ thuật)
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
| `hub_id` | INT / UUID | **FK** -> `hubs.id`, Nullable | **[MỚI]** Trạm làm việc (dành cho Technician) |
| `username` | VARCHAR(50) | Unique, Not Null | Tên đăng nhập |
| `password_hash` | VARCHAR(255) | Not Null | Mật khẩu đã mã hóa |
| `full_name` | VARCHAR(100) | Not Null | Họ và tên |
| `email` | VARCHAR(100) | Unique, Nullable | Email liên hệ |
| `is_active` | BOOLEAN | Default TRUE | Trạng thái tài khoản |
| `created_at` | TIMESTAMP | Default NOW() | |

### B. Nhóm Quản lý Đội bay & Thiết bị (Fleet Management)

**4. Bảng `drones`** (Thông tin máy bay)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID máy bay |
| `name` | VARCHAR(100) | Not Null | Tên máy bay |
| `model` | VARCHAR(100) | Not Null | Đời máy |
| `current_hub_id`| INT / UUID | **FK** -> `hubs.id`, Nullable | **[MỚI]** Hiện đang nằm tại Hub nào |
| `operational_status`| VARCHAR(50) | Enum | **[CẢI TIẾN]** `AVAILABLE`, `MAINTENANCE`, `BATTERY_REPLACEMENT`, `IN_MISSION` |
| `battery_level_pct`| INT | Nullable | **[MỚI]** Phần trăm pin hiện tại (để Technician xem nhanh) |
| `utilization_pct` | DECIMAL(5,2)| Nullable | **[MỚI]** Hiệu suất sử dụng |
| `created_at` | TIMESTAMP | Default NOW() | |

**5. Bảng `batteries`** (Quản lý vòng đời pin - GIỮ NGUYÊN cho kho/vận hành)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID của viên pin |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | Lắp trên Drone nào (Null = trong kho) |
| `serial_number` | VARCHAR(100) | Unique, Not Null | Số seri pin |
| `capacity_wh` | FLOAT | Not Null | Dung lượng (Watt-hour) |
| `status` | VARCHAR(50) | Enum | `Active`, `Replaced` |

### C. Nhóm Chuyến bay & Thanh toán (Mission & Payment)

**6. Bảng `locations`** (Quản lý điểm bay chung)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID địa điểm |
| `name` | VARCHAR(255) | Not Null | Tên địa điểm |
| `latitude` | DECIMAL(10,8)| Not Null | Vĩ độ |
| `longitude` | DECIMAL(11,8)| Not Null | Kinh độ |
| `type` | VARCHAR(50) | Enum | `CustomerAddress`, `Hub` |

**7. Bảng `missions`** (Luồng công việc chính yếu)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | UUID / INT | **PK** | ID chuyến bay |
| `customer_id` | VARCHAR(50) | **FK** -> `users.id` | Người tạo yêu cầu |
| `operator_id` | VARCHAR(50) | **FK** -> `users.id`, Nullable | Người duyệt |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | Máy bay phân công |
| `battery_id` | INT | **FK** -> `batteries.id`, Nullable| Pin được sử dụng |
| `pickup_location_id`| INT | **FK** -> `locations.id` | Điểm nhận hàng |
| `dropoff_location_id`| INT | **FK** -> `locations.id` | Điểm giao hàng |
| `destination_hub_id`| INT / UUID | **FK** -> `hubs.id`, Nullable | **[MỚI]** Trạm đích (nếu cần Drone bay về Hub) |
| `delivery_fee` | FLOAT | Not Null | Phí giao hàng |
| `status` | VARCHAR(50) | Enum | `Awaiting Payment`, `Approved`, `Flying`, `Completed`... |
| `arrived_at` | TIMESTAMP | Nullable | **[MỚI]** Giờ hạ cánh tại Hub |
| `arrival_confirmed_by`| VARCHAR(50)| **FK** -> `users.id`, Nullable| **[MỚI]** Technician xác nhận nhận Drone |
| `approval_deadline`| TIMESTAMP| Nullable | Hỗ trợ dữ liệu cho API trả về làm hiệu ứng đếm ngược trên UI và làm mốc thời gian cho Worker tự động hủy đơn |


*(Các bảng `payments` và `mission_reports` được giữ nguyên như thiết kế ban đầu)*

### D. Nhóm Dữ liệu AI & Phân tích (Analytics)
*(Các bảng `telemetry_logs`, `ai_predictions`, `ai_recommendations` giữ nguyên)*

### E. Nhóm Sự cố & Bảo trì (Maintenance, Alerts, Work Orders)

**8. Bảng `incidents`** (Sự cố)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID sự cố |
| `mission_id` | UUID / INT | **FK** -> `missions.id`, Nullable | |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | |
| `severity` | VARCHAR(50) | Enum | |
| `requires_technical_inspection`| BOOLEAN| Default FALSE | **[MỚI]** Bật cờ này để báo Technician kiểm tra |
| `description` | TEXT | Not Null | |
| `status` | VARCHAR(50) | Enum | `Open`, `Investigating`, `Closed` |

**9. Bảng `maintenance_schedules`** (BẢNG MỚI - Lịch bảo dưỡng định kỳ)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT / UUID | **PK** | ID lịch trình |
| `drone_id` | INT | **FK** -> `drones.id` | Áp dụng cho Drone nào |
| `interval_days` | INT | | Chu kỳ (ngày) |
| `interval_flight_hours`| DECIMAL | | Chu kỳ (giờ bay) |
| `last_inspection_at`| TIMESTAMP | | Lần kiểm tra cuối |
| `next_inspection_at`| TIMESTAMP | | Hạn kiểm tra tiếp theo |

**10. Bảng `maintenance_alerts`** (BẢNG MỚI - Cảnh báo cần bảo trì)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT / UUID | **PK** | ID Alert |
| `drone_id` | INT | **FK** -> `drones.id` | Drone cần xử lý |
| `source` | VARCHAR(30) | Enum | `SCHEDULE`, `FLIGHT_EVENT`, `INCIDENT` |
| `incident_id` | INT | **FK** -> `incidents.id`| Từ sự cố nào (nếu có) |
| `status` | VARCHAR(20) | Enum | `PENDING`, `HANDLED` |

**11. Bảng `work_orders`** (Đơn yêu cầu bảo trì - ĐƯỢC CẢI TIẾN)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID phiếu bảo trì |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | |
| `battery_id` | INT | **FK** -> `batteries.id`, Nullable| (Tương thích DB cũ) |
| `alert_id` | INT / UUID | **FK** -> `maintenance_alerts.id`| **[MỚI]** Trỏ về Alert |
| `priority` | VARCHAR(20) | Enum | **[MỚI]** `HIGH`, `MEDIUM`, `LOW` |
| `technician_id` | VARCHAR(50) | **FK** -> `users.id` | Người phụ trách |
| `issue_description` | TEXT | Not Null | Mô tả vấn đề |
| `status` | VARCHAR(50) | Enum | **[CẢI TIẾN]** `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED` |
| `scheduled_at` | TIMESTAMP | Nullable | **[MỚI]** Ngày hẹn |
| `completed_at` | TIMESTAMP | Nullable | **[CẢI TIẾN]** Thay cho `resolved_at` |

**12. Bảng `work_order_logs`, `maintenance_records`, `maintenance_inspection_items`**
*(3 Bảng mới được thêm vào y hệt thiết kế schema Technician để lưu vết và checklist chẩn đoán chi tiết sau khi hoàn thành Work Order)*

### F. Nhóm Giao tiếp (Communication)

**13. Bảng `notifications`** (Quản lý thông báo - ĐƯỢC CẢI TIẾN)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | **PK**, Auto Increment | |
| `user_id` | VARCHAR(50) | **FK** -> `users.id` | Người nhận |
| `type` | VARCHAR(50) | Enum | **[MỚI]** Bổ sung `INCOMING`, `BATTERY`, `WORK_ORDER`, `OVERDUE` |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | **[MỚI]** Tham chiếu Drone |
| `work_order_id` | INT | **FK** -> `work_orders.id`, Nullable| **[MỚI]** Tham chiếu WO |
| `mission_id` | UUID / INT | **FK** -> `missions.id`, Nullable| **[MỚI]** Tham chiếu Mission |
| `is_read` | BOOLEAN | Default FALSE | |

---

## II. Luồng nghiệp vụ Technician tương thích với hệ thống cũ

### 1. Luồng Quản lý Pin không phá vỡ logic cũ (Hybrid Battery Management)
*   **Vận hành cũ:** Hệ thống vẫn giữ nguyên bảng `batteries` để Operator gắn pin (`battery_id`) vào chuyến bay (`missions`).
*   **Cải tiến Technician:** Dựa theo tài liệu tham khảo `Technician.md`, thay vì Technician phải tra cứu bảng `batteries` phức tạp, Backend sẽ tự động cập nhật phần trăm pin live vào cột mới `drones.battery_level_pct`. Technician chỉ cần nhìn vào bảng `drones` là biết trạng thái pin hiện tại. Nếu dưới 40%, gửi Alert cho Technician thay pin.

### 2. Luồng Xác nhận Drone tới Hub (Incoming Flow kết hợp Missions)
*   Khi Operator duyệt một `mission` yêu cầu Drone bay tới Hub, `destination_hub_id` được set.
*   Khi hạ cánh, Technician sẽ nhận Notification `INCOMING`. 
*   Technician bấm "Confirm Arrival", Backend sẽ update `missions.arrival_confirmed_by` và cập nhật vị trí máy bay `drones.current_hub_id` tương thích hoàn toàn với luồng bay cũ.

### 3. Luồng Sự cố kích hoạt Bảo trì (Incident to Alert)
*   Sự cố (`incidents`) do Operator/AI báo cáo vẫn hoạt động bình thường. 
*   Nếu sự cố đó có cờ `requires_technical_inspection = true`, một Trigger/Worker sẽ tự động sinh thêm 1 bản ghi vào bảng mới `maintenance_alerts` để đẩy sang màn hình của Technician xử lý, giữ nguyên vòng đời của Incident bên luồng Vận hành.
