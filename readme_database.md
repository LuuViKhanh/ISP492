# Cấu trúc Cơ sở dữ liệu - DroneOptAI (FA26IS01)

Tài liệu này định nghĩa cấu trúc cơ sở dữ liệu và các luồng tương tác chuẩn xác theo yêu cầu thiết kế của hệ thống **Energy-Efficient Drone Delivery System**. Cấu trúc này bao gồm đầy đủ các tính năng Trí tuệ nhân tạo (AI), Vận hành (Operations), Quản trị dữ liệu (Data Governance) và đã được **tích hợp thêm phân hệ Thanh toán (Payment & Revenue)** để hoàn thiện hệ thống theo mô hình B2C thực tế.

---

## I. Cấu trúc các bảng (Tables)

### A. Nhóm Quản lý Người dùng & Phân quyền (Auth & Users)

**1. Bảng `roles`** (Lưu trữ các nhóm quyền)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | ID quyền |
| `role_name` | VARCHAR(50) | Unique, Not Null | Tên quyền (Admin, Operator, Technician, Customer) |
| `description` | VARCHAR(255) | Nullable | Mô tả chi tiết |

**2. Bảng `users`** (Lưu trữ tài khoản)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | ID người dùng |
| `role_id` | VARCHAR(50) | **FK** -> `roles.id` | Quyền của user |
| `username` | VARCHAR(50) | Unique, Not Null | Tên đăng nhập |
| `password_hash` | VARCHAR(255) | Not Null | Mật khẩu đã mã hóa |
| `full_name` | VARCHAR(100) | Not Null | Họ và tên |
| `email` | VARCHAR(100) | Unique, Not Null | Email liên hệ |
| `is_active` | BOOLEAN | Default TRUE | Trạng thái tài khoản |
| `created_at` | TIMESTAMP | Default NOW() | Thời gian tạo tài khoản |

### B. Nhóm Quản lý Đội bay & Thiết bị (Fleet Management)

**3. Bảng `drones`** (Thông tin máy bay)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID máy bay |
| `name` | VARCHAR(100) | Not Null | Tên máy bay |
| `model` | VARCHAR(100) | Not Null | Đời máy (VD: DJI Matrice 300) |
| `payload_capacity_kg`| FLOAT | Not Null | Sức chở tối đa (kg) |
| `max_speed` | FLOAT | Not Null | Tốc độ tối đa |
| `status` | VARCHAR(50) | Enum | `Available`, `In Mission`, `Maintenance`, `Retired` |
| `created_at` | TIMESTAMP | Default NOW() | Ngày nhập hệ thống |

**4. Bảng `batteries`** (Quản lý vòng đời pin)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID của viên pin |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | Lắp trên Drone nào (Null = trong kho) |
| `serial_number` | VARCHAR(100) | Unique, Not Null | Số seri pin |
| `capacity_wh` | FLOAT | Not Null | Dung lượng (Watt-hour) |
| `status` | VARCHAR(50) | Enum | `Active`, `Degraded` (Chai), `Replaced` (Thay thế) |

### C. Nhóm Chuyến bay & Thanh toán (Mission & Payment)

**5. Bảng `locations`** (Quản lý các điểm bay/Hub)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID địa điểm |
| `name` | VARCHAR(255) | Not Null | Tên địa điểm |
| `latitude` | DECIMAL(10,8)| Not Null | Vĩ độ |
| `longitude` | DECIMAL(11,8)| Not Null | Kinh độ |
| `type` | VARCHAR(50) | Enum | `Hub` (Trạm), `CustomerAddress` |

**6. Bảng `missions`** (Luồng công việc chính yếu)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | UUID / INT | **PK** | ID chuyến bay |
| `customer_id` | VARCHAR(50) | **FK** -> `users.id` | Người tạo yêu cầu |
| `operator_id` | VARCHAR(50) | **FK** -> `users.id`, Nullable | Người duyệt chuyến bay |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | Máy bay được phân công |
| `battery_id` | INT | **FK** -> `batteries.id`, Nullable | Pin được sử dụng |
| `pickup_location_id` | INT | **FK** -> `locations.id` | Điểm nhận hàng |
| `dropoff_location_id`| INT | **FK** -> `locations.id` | Điểm giao hàng |
| `payload_weight` | FLOAT | Not Null | Khối lượng hàng (kg) |
| `distance_m` | FLOAT | Nullable | Khoảng cách tuyến đường (mét) |
| `delivery_fee` | FLOAT | Not Null | Phí giao hàng tính toán cho đơn này |
| `status` | VARCHAR(50) | Enum | `Awaiting Payment`, `Pending Approval`, `Approved`, `Rejected`, `Flying`, `Completed`, `Cancelled` |
| `scheduled_time` | TIMESTAMP | Not Null | Thời gian bay dự kiến |
| `start_time` | TIMESTAMP | Nullable | Thời gian cất cánh thực tế |
| `end_time` | TIMESTAMP | Nullable | Thời gian hạ cánh thực tế |

**7. Bảng `payments`** (Quản lý giao dịch thanh toán - BẢNG MỚI)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(50) | **PK** | ID giao dịch (VD: PAY-123) |
| `mission_id` | UUID / INT | **FK** -> `missions.id` | Thanh toán cho chuyến bay nào |
| `amount` | FLOAT | Not Null | Số tiền thanh toán (bằng `delivery_fee`) |
| `payment_method` | VARCHAR(50) | Enum | `VNPay`, `Momo`, `Credit Card`, `Cash` |
| `transaction_id` | VARCHAR(100) | Nullable | Mã giao dịch đối tác (Mã thẻ, mã VNPay) |
| `status` | VARCHAR(50) | Enum | `Pending`, `Success`, `Failed`, `Refunded` |
| `created_at` | TIMESTAMP | Default NOW() | Thời gian tạo yêu cầu thanh toán |
| `paid_at` | TIMESTAMP | Nullable | Thời điểm thanh toán thành công |

**8. Bảng `mission_reports`** (Lưu file báo cáo chuyến bay)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID báo cáo |
| `mission_id` | UUID / INT | **FK** -> `missions.id` | Chuyến bay tương ứng |
| `report_url_path` | VARCHAR(500) | Not Null | Đường dẫn tải file PDF/Word |
| `generated_at` | TIMESTAMP | Default NOW() | Thời gian xuất file |

### D. Nhóm Dữ liệu AI & Phân tích (Analytics & Explainable AI)

**9. Bảng `telemetry_logs`** (Lưu trữ log từ Drone khi bay)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | **PK**, Auto Increment | ID log |
| `mission_id` | UUID / INT | **FK** -> `missions.id` | Của chuyến bay nào |
| `timestamp` | TIMESTAMP | Not Null | Mốc thời gian |
| `latitude` | DECIMAL(10,8)| Not Null | Vĩ độ hiện tại |
| `longitude` | DECIMAL(11,8)| Not Null | Kinh độ hiện tại |
| `altitude` | FLOAT | Not Null | Độ cao (m) |
| `speed` | FLOAT | Not Null | Tốc độ (m/s) |
| `battery_voltage` | FLOAT | Nullable | Điện áp pin |
| `energy_consumed_wh` | FLOAT | Nullable | Năng lượng đã tiêu thụ |
| `wind_speed` | FLOAT | Nullable | Tốc độ gió |

**10. Bảng `ai_predictions`** (Kết quả dự đoán của AI)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID dự đoán |
| `mission_id` | UUID / INT | **FK** -> `missions.id` | Chuyến bay được dự đoán |
<!-- | `ml_model_id` | INT | **FK** -> `ml_models.id` | Model AI phiên bản nào đã dùng | -->
| `estimated_energy_wh`| FLOAT | Not Null | Dự đoán tiêu thụ năng lượng |
| `estimated_duration_m`| FLOAT | Not Null | Dự đoán thời gian bay (phút) |
| `risk_level` | VARCHAR(50) | Enum | Mức rủi ro (`Low`, `Medium`, `High`) |
| `shap_values_json` | JSONB | Nullable | Lưu giá trị Explainable AI (SHAP) |
| `predicted_at` | TIMESTAMP | Default NOW() | Thời gian thực hiện dự đoán |

**11. Bảng `ai_recommendations`** (Hệ thống gợi ý hành động)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID gợi ý |
| `mission_id` | UUID / INT | **FK** -> `missions.id`, Nullable | Có thể thuộc về 1 chuyến bay cụ thể |
| `category` | VARCHAR(50) | Enum | 1 trong 7 loại: Mission Planning, Maintenance, Battery Health, Payload Optimisation, Weather Alert, Risk Assessment, Operator Assignment |
| `content` | TEXT | Not Null | Nội dung khuyến nghị |
| `is_applied` | BOOLEAN | Default FALSE | User đã áp dụng khuyến nghị chưa |
| `created_at` | TIMESTAMP | Default NOW() | Thời gian tạo khuyến nghị |

### E. Nhóm Sự cố & Bảo trì (Maintenance & Incidents)

**12. Bảng `incidents`** (Quản lý sự kiện bất ngờ / tai nạn)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID sự cố |
| `mission_id` | UUID / INT | **FK** -> `missions.id`, Nullable | Sự cố của chuyến bay nào |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | Sự cố liên quan Drone nào |
| `reporter_id` | VARCHAR(50) | **FK** -> `users.id` | Người báo cáo |
| `severity` | VARCHAR(50) | Enum | Mức độ (`Low`, `Medium`, `Critical`) |
| `description` | TEXT | Not Null | Mô tả sự cố |
| `status` | VARCHAR(50) | Enum | `Open`, `Investigating`, `Closed` |
| `reported_at` | TIMESTAMP | Default NOW() | Thời gian báo cáo |

**13. Bảng `work_orders`** (Đơn yêu cầu bảo trì / thay pin)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID phiếu bảo trì |
| `drone_id` | INT | **FK** -> `drones.id`, Nullable | Bảo trì Drone |
| `battery_id` | INT | **FK** -> `batteries.id`, Nullable| Kiểm tra / Thay pin |
| `technician_id` | VARCHAR(50) | **FK** -> `users.id` | Kỹ thuật viên phụ trách |
| `issue_description` | TEXT | Not Null | Mô tả vấn đề cần sửa |
| `action_taken` | TEXT | Nullable | Hành động đã xử lý / Linh kiện thay |
| `status` | VARCHAR(50) | Enum | `Pending`, `In Progress`, `Completed` |
| `resolved_at` | TIMESTAMP | Nullable | Thời gian hoàn tất |

### F. Nhóm Quản trị Dữ liệu (Data Governance - Dành cho Admin)
<!-- 
**14. Bảng `ml_models`** (Quản trị Model Versioning)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID phiên bản Model |
| `version_tag` | VARCHAR(50) | Unique, Not Null | Tên phiên bản (VD: `v1.0-XGBoost`) |
| `description` | TEXT | Nullable | Mô tả sự thay đổi của model |
| `is_active` | BOOLEAN | Default FALSE | Model nào đang được dùng thực tế |
| `deployed_at` | TIMESTAMP | Default NOW() | Ngày deploy model | -->

<!-- **15. Bảng `datasets`** (Quản trị Dataset Versioning)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | INT | **PK**, Auto Increment | ID phiên bản tập dữ liệu |
| `version_tag` | VARCHAR(50) | Unique, Not Null | Tên version (VD: `dataset-2026-Q1`) |
| `description` | TEXT | Nullable | Mô tả tập dữ liệu |
| `record_count` | INT | Not Null | Số lượng records có trong dataset |
| `created_at` | TIMESTAMP | Default NOW() | Thời gian tạo dataset | -->

**16. Bảng `audit_logs`** (Lưu vết hành động - Auditability)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | **PK**, Auto Increment | ID log |
| `user_id` | VARCHAR(50) | **FK** -> `users.id`, Nullable | Người thực hiện (Null: hệ thống tự làm) |
| `action` | VARCHAR(50) | Not Null | Loại thao tác (`CREATE`, `UPDATE`, `APPROVE`) |
| `resource_table` | VARCHAR(50) | Not Null | Bảng bị tác động (VD: `missions`) |
| `details_json` | JSONB | Nullable | Dữ liệu cũ/mới trước và sau khi đổi |
| `created_at` | TIMESTAMP | Default NOW() | Thời gian thực hiện thao tác |

### G. Nhóm Giao tiếp (Communication)

**17. Bảng `notifications`** (Quản lý thông báo cho người dùng)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | **PK**, Auto Increment | ID thông báo |
| `user_id` | VARCHAR(50) | **FK** -> `users.id` | Người nhận (Customer, Operator, Technician) |
| `title` | VARCHAR(100) | Not Null | Tiêu đề thông báo |
| `message` | TEXT | Not Null | Nội dung chi tiết |
| `type` | VARCHAR(50) | Enum | `Mission_Status`, `Payment`, `System_Alert`, `AI_Warning`, `Maintenance` |
| `reference_id`| VARCHAR(100) | Nullable | Lưu ID liên kết (Ví dụ: mission_id, payment_id) |
| `is_read` | BOOLEAN | Default FALSE | Trạng thái đã đọc hay chưa |
| `created_at` | TIMESTAMP | Default NOW() | Thời gian gửi thông báo |

---

## II. Luồng nghiệp vụ tương tác (Functional Flows)

Để code Backend/Frontend dễ theo dõi và không bỏ sót khóa ngoại (Foreign Keys), đây là tóm tắt sự tương tác giữa các bảng theo nghiệp vụ chính, bao gồm cả phân hệ Thanh toán và Thông báo:

### 1. Luồng Đăng nhập & Phân quyền (Auth & RBAC)
*   **Tương tác:** `users` ↔️ `roles`
*   **Quy trình:** Khi user đăng nhập, hệ thống xác thực ở bảng `users`, sau đó tự động JOIN qua bảng `roles` (qua `role_id`) để biết User là Admin, Operator, Technician hay Customer.
- Password phải được mã hóa và giải mã hóa trong code BE còn database chỉ lưu password_hash (tức là pass đã mã hóa)
- username và email là 2 giá trị độc nhất không thể trùng
- email có thể null. Nếu được thì có thể tạo xác thực bằng email nếu email không null

### 2. Luồng Đặt hàng & Thanh toán (Customer Workflow)
*   **Tương tác:** `users` ➡️ `locations` ➡️ `missions` ➡️ `payments` ➡️ `notifications`
*   **Quy trình:**
    *   **Tạo đơn:** Customer chọn điểm đi/đến. Backend tính phí vận chuyển (`delivery_fee`) và lưu vào `missions` với trạng thái `Awaiting Payment`.
    *   **Thanh toán:** Customer trả tiền (VNPay/Momo). Backend tạo giao dịch trong `payments`. Khi thành công, đổi `payments.status` thành `Success` và cập nhật `missions.status` thành `Pending Approval`. Đồng thời sinh ra `notifications` cho Customer ("Thanh toán thành công") và Operator ("Có đơn hàng mới chờ duyệt").

### 3. Luồng Duyệt chuyến bay & Hoàn tiền (Operator Workflow)
*   **Tương tác:** `users` (Operator) ➡️ `missions` ↔️ `payments`, `drones`, `batteries` ➡️ `notifications`
*   **Quy trình:**
    *   **Duyệt đơn:** Operator chọn đơn `Pending Approval`, phân bổ Drone và Pin. Đổi `missions.status` thành `Approved` và gán `operator_id`, `drone_id`, `battery_id`. Gửi `notifications` cho Customer ("Đơn hàng đã được duyệt").
    *   **Hủy & Hoàn tiền:** Nếu thời tiết quá xấu, Operator bấm từ chối. `missions.status` thành `Rejected` và `payments.status` thành `Refunded` (kích hoạt API trả tiền cho khách). Gửi `notifications` cho Customer ("Đơn hàng bị hủy, tiền đang được hoàn lại").

### 4. Luồng Quản lý Đội bay & Gắn Pin (Fleet Lifecycle)
*   **Tương tác:** `drones` ↔️ `batteries`
*   **Quy trình:** Khi Technician gắn Pin cho Drone, hệ thống `UPDATE` bảng `batteries` bằng cách gán giá trị `drone_id` vào viên pin đó. Nếu tháo cất kho, set `drone_id` thành NULL.

### 5. Luồng Thống kê Doanh thu (Admin Revenue)
*   **Tương tác:** `payments` ➡️ Admin Dashboard
*   **Quy trình:** Để xem "Doanh thu hôm nay" hoặc "Tổng doanh thu", Backend chỉ cần Query `SUM(amount)` từ bảng `payments` điều kiện `status = 'Success'` và thời gian tương ứng. Không cần query qua bảng `missions`.

### 6. Luồng Dự đoán AI & Đưa ra lời khuyên (AI Prediction)
*   **Tương tác:** `missions` ➡️ `ai_predictions` ➡️ `ml_models` và `missions` ➡️ `ai_recommendations` ➡️ `notifications`
*   **Quy trình:**
    *   **Dự đoán:** Backend lấy data từ `missions`, gọi AI (lấy phiên bản từ `ml_models`). Kết quả lưu vào `ai_predictions` (có `mission_id`).
    *   **Gợi ý:** Nếu AI phát hiện bất thường, sinh cảnh báo lưu vào `ai_recommendations`. Tự động tạo `notifications` loại `AI_Warning` gửi đến Operator.

### 7. Luồng Bay thực tế & Báo cáo (Telemetry & Report)
*   **Tương tác:** `missions` ➡️ `telemetry_logs` ➡️ `mission_reports` ➡️ `notifications`
*   **Quy trình:**
    *   Khi cất cánh (`Flying`), tọa độ/pin trả về mỗi giây được `INSERT` vào `telemetry_logs`. Gửi `notifications` cho Customer ("Chuyến bay đã cất cánh").
    *   Khi hạ cánh (`Completed`), hệ thống gen file PDF báo cáo và lưu đường dẫn vào `mission_reports` để Customer tải về. Gửi `notifications` cho Customer ("Chuyến bay hoàn tất, xem báo cáo").

### 8. Luồng Sự cố, Bảo trì & Audit (Incidents, Maintenance & Audit)
*   **Tương tác:** `incidents` ➡️ `work_orders` ➡️ `notifications` và mọi thao tác ➡️ `audit_logs`
*   **Quy trình:**
    *   Sự cố xảy ra lưu vào `incidents`. Sinh lệnh bảo trì `work_orders` giao cho Technician. Tạo `notifications` gửi cho Technician ("Bạn được giao 1 lệnh bảo trì mới") và Operator ("Có sự cố đang được xử lý").
    *   Mọi thao tác thay đổi trạng thái (Duyệt, Hủy, Thanh toán) đều sinh thêm một dòng trong `audit_logs` để Admin kiểm soát.
