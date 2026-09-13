# Cấu trúc Cơ sở dữ liệu - DroneOptAI (FA26IS01)

Tài liệu này định nghĩa cấu trúc cơ sở dữ liệu và các luồng tương tác chuẩn xác theo yêu cầu thiết kế của hệ thống **Energy-Efficient Drone Delivery System**. Cấu trúc này bao gồm đầy đủ các tính năng Trí tuệ nhân tạo (AI), Vận hành (Operations), Quản trị dữ liệu (Data Governance) và đã được **tích hợp thêm phân hệ Thanh toán (Payment & Revenue)** để hoàn thiện hệ thống theo mô hình B2C thực tế.

---

## Luồng nghiệp vụ tương tác (Functional Flows)

Để code Backend/Frontend dễ theo dõi và không bỏ sót khóa ngoại (Foreign Keys), đây là tóm tắt sự tương tác giữa các bảng theo nghiệp vụ chính, bao gồm cả phân hệ Thanh toán:

### 1. Luồng Đăng nhập & Phân quyền (Auth & RBAC)
*   **Tương tác:** `users` ↔️ `roles`
*   **Quy trình:** Khi user đăng nhập, hệ thống xác thực ở bảng `users`, sau đó tự động JOIN qua bảng `roles` (qua `role_id`) để biết User là Admin, Operator, Technician hay Customer.
- Password phải được mã hóa và giải mã hóa trong code BE còn database chỉ lưu password_hash (tức là pass đã mã hóa)
- username và email là 2 giá trị độc nhất không thể trùng
- email có thể null. Nếu được thì có thể tạo xác thực bằng email nếu email không null

### 2. Luồng Đặt hàng & Thanh toán (Customer Workflow)
*   **Tương tác:** `users` ➡️ `locations` ➡️ `missions` ➡️ `payments`
*   **Quy trình:**
    *   **Tạo đơn:** Customer chọn điểm đi/đến. Backend tính phí vận chuyển (`delivery_fee`) và lưu vào `missions` với trạng thái `Awaiting Payment`.
    *   **Thanh toán:** Customer trả tiền (VNPay/Momo). Backend tạo giao dịch trong `payments`. Khi thành công, đổi `payments.status` thành `Success` và cập nhật `missions.status` thành `Pending Approval` (Đợi Operator xử lý).

### 3. Luồng Duyệt chuyến bay & Hoàn tiền (Operator Workflow)
*   **Tương tác:** `users` (Operator) ➡️ `missions` ↔️ `payments`, `drones`, `batteries`.
*   **Quy trình:**
    *   **Duyệt đơn:** Operator chọn đơn `Pending Approval`, phân bổ Drone và Pin. Đổi `missions.status` thành `Approved` và gán `operator_id`, `drone_id`, `battery_id`.
    *   **Hủy & Hoàn tiền:** Nếu thời tiết quá xấu, Operator bấm từ chối. `missions.status` thành `Rejected` và `payments.status` thành `Refunded` (kích hoạt API trả tiền cho khách).

### 4. Luồng Quản lý Đội bay & Gắn Pin (Fleet Lifecycle)
*   **Tương tác:** `drones` ↔️ `batteries`
*   **Quy trình:** Khi Technician gắn Pin cho Drone, hệ thống `UPDATE` bảng `batteries` bằng cách gán giá trị `drone_id` vào viên pin đó. Nếu tháo cất kho, set `drone_id` thành NULL.

### 5. Luồng Thống kê Doanh thu (Admin Revenue)
*   **Tương tác:** `payments` ➡️ Admin Dashboard
*   **Quy trình:** Để xem "Doanh thu hôm nay" hoặc "Tổng doanh thu", Backend chỉ cần Query `SUM(amount)` từ bảng `payments` điều kiện `status = 'Success'` và thời gian tương ứng. Không cần query qua bảng `missions`.

### 6. Luồng Dự đoán AI & Đưa ra lời khuyên (AI Prediction)
*   **Tương tác:** `missions` ➡️ `ai_predictions` ➡️ `ml_models` và `missions` ➡️ `ai_recommendations`
*   **Quy trình:**
    *   **Dự đoán:** Backend lấy data từ `missions`, gọi AI (lấy phiên bản từ `ml_models`). Kết quả lưu vào `ai_predictions` (có `mission_id`).
    *   **Gợi ý:** Nếu AI phát hiện bất thường, sinh cảnh báo lưu vào `ai_recommendations` (gắn với `mission_id` và 1 trong 7 `category` chuẩn).

### 7. Luồng Bay thực tế & Báo cáo (Telemetry & Report)
*   **Tương tác:** `missions` ➡️ `telemetry_logs` ➡️ `mission_reports`
*   **Quy trình:**
    *   Khi cất cánh (`Flying`), tọa độ/pin trả về mỗi giây được `INSERT` vào `telemetry_logs`.
    *   Khi hạ cánh (`Completed`), hệ thống gen file PDF báo cáo và lưu đường dẫn vào `mission_reports` để Customer tải về.

### 8. Luồng Sự cố, Bảo trì & Audit (Incidents, Maintenance & Audit)
*   **Tương tác:** `incidents` ➡️ `work_orders` và mọi thao tác ➡️ `audit_logs`
*   **Quy trình:**
    *   Sự cố xảy ra lưu vào `incidents`. Sinh lệnh bảo trì `work_orders` giao cho Technician.
    *   Mọi thao tác thay đổi trạng thái (Duyệt, Hủy, Thanh toán) đều sinh thêm một dòng trong `audit_logs` để Admin kiểm soát.
