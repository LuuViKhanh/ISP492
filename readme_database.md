# Database Schema Documentation
Đây là tài liệu Single Source of Truth cho cấu trúc cơ sở dữ liệu của dự án Drone-backend, được tự động cập nhật và đồng bộ theo models và các bản migration mới nhất (tuân thủ quy định `GEMINI.md`).

> **Kiến trúc Cơ sở dữ liệu Chuẩn hóa (Database Schema - 25 Bảng)**:
> Hệ thống gồm **25 bảng chính thức** lưu trữ và điều phối toàn bộ chu trình vận hành bay, phân phối trạm, bảo trì, quản lý pin, vật tư linh kiện, yêu cầu thay thế, đơn hàng, đánh giá giao hàng, sổ địa chỉ, thống kê hoạt động drone theo ngày và nhật ký hệ thống.
> *(Các bảng cũ `audit_logs`, `hub_inventories`, `inventory_transactions` đã được thay thế/đổi tên hoàn toàn sang `activity_logs`, `system_logs`, `hub_supplies`, `supply_transactions` và xóa khỏi Database).*

> **Lưu ý về Múi giờ (Timezone)**: 
> Toàn bộ các trường kiểu thời gian (`DATETIME`, `TIMESTAMP`, `TIMESTAMPTZ`) trong hệ thống được quản lý, khởi tạo và ghi nhận đồng bộ theo múi giờ chuẩn Việt Nam (GMT+7 / `Asia/Ho_Chi_Minh`) thông qua module `app.core.timezone`.

> **Lưu ý về Định dạng Mã Drone & Pin (Drone & Battery ID Format)**:
> - **Drone (`drones.id`)**: `{ModelCode}-DRNXXXX` (`M100-DRN0001` cho DJI Matrice 100, `X6P-DRN0002` cho Aurelia X6 Pro, `FC15-DRN0005` cho DJI FlyCart 15 Heavy).
> - **Battery (`batteries.id`)**: `{BatteryCode}-BATXXXX` (`TB48-BAT0001` cho DJI TB48D, `UP12-BAT0002` cho Aurelia UltraPower 12S, `DB20-BAT0005` cho DJI DB2000).
> - Toàn bộ khóa chính và các cột tham chiếu `drone_id` / `battery_id` ở các bảng liên quan đều sử dụng kiểu `VARCHAR(50)`.

---

## I. Cấu trúc 25 Bảng Hệ Thống (Database Tables)

### 1. Bảng accounts (trước đây là users)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable |
| role_id | VARCHAR | Nullable |
| password_hash | VARCHAR | Nullable |
| full_name | VARCHAR | Non-nullable |
| email | VARCHAR | Unique, Non-nullable |
| is_active | BOOLEAN | Non-nullable, Default `true` |
| created_at | TIMESTAMPTZ | Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Nullable |
| reset_token | VARCHAR(255) | Nullable (Hợp nhất từ password_reset_tokens) |
| reset_token_expires_at | TIMESTAMPTZ | Nullable |

### 2. Bảng activity_logs (thay thế audit_logs cho thao tác người dùng)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| user_id | VARCHAR | Nullable |
| user_email | VARCHAR | Nullable |
| role | VARCHAR(50) | Nullable |
| action | VARCHAR(100) | Non-nullable |
| resource_type | VARCHAR(100) | Nullable |
| resource_id | VARCHAR | Nullable |
| description | TEXT | Nullable |
| status | VARCHAR(20) | Non-nullable, Default `'success'` |
| details_json | JSONB | Nullable |
| created_at | TIMESTAMPTZ | Non-nullable, Default `now()` |

### 3. Bảng ai_predictions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| estimated_energy_wh | FLOAT | Non-nullable |
| estimated_duration_m | FLOAT | Non-nullable |
| risk_level | VARCHAR(20) | Non-nullable |
| recommendation_content | TEXT | Nullable (Hợp nhất từ ai_recommendations) |
| is_recommendation_applied | BOOLEAN | Nullable, Default `true` |
| predicted_at | TIMESTAMPTZ | Non-nullable |

### 4. Bảng batteries
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable (Chuẩn `{BatteryCode}-BATXXXX`: `TB48-BAT0001`, `UP12-BAT0002`, `DB20-BAT0005`) |
| drone_id | VARCHAR(50) | **FK** (`drones.id`), Nullable (`ON UPDATE CASCADE`) |
| serial_number | VARCHAR(100) | Unique, Non-nullable (`SN-BAT-XXXX`) |
| capacity_wh | FLOAT | Non-nullable |
| status | VARCHAR(20) | Non-nullable (`batteries_status`) |
| current_hub_id | INTEGER | **FK** (`hubs.id`), Nullable |
| charge_level_pct | BIGINT | Nullable |
| create_at | TIMESTAMPTZ | Nullable |
| update_at | TIMESTAMPTZ | Nullable |
| battery_model | VARCHAR(100) | Nullable (`DJI TB48D` / `Aurelia UltraPower 12S` / `DJI DB2000`) |
| last_charged_at | TIMESTAMP | Nullable |
| work_order_id | INTEGER | **FK** (`work_orders.id`), Nullable |

### 5. Bảng battery_model_catalog
Catalog model pin chuẩn với giá nhập và danh sách dòng drone tương thích.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| name | VARCHAR(100) | Unique, Non-nullable |
| compatible_drone_models | JSONB | Nullable |
| capacity_wh | FLOAT | Non-nullable |
| unit_cost | FLOAT | Nullable (VNĐ / viên) |
| description | TEXT | Nullable |
| is_active | BOOLEAN | Non-nullable, Default `true` |
| created_at | TIMESTAMPTZ | Nullable |

### 6. Bảng drones
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable (Chuẩn `{ModelCode}-DRNXXXX`: `M100-DRN0001`, `X6P-DRN0002`, `FC15-DRN0005`) |
| name | VARCHAR(100) | Non-nullable |
| model | VARCHAR(100) | Non-nullable |
| serial_number | VARCHAR(100) | Unique, Non-nullable (`SN-DRN-XXXX`) |
| payload_capacity_kg | FLOAT | Non-nullable |
| max_speed | FLOAT | Non-nullable |
| operational_status | VARCHAR(20) | Non-nullable (`drone_status`) |
| current_hub_id | BIGINT | Nullable |
| battery_level_pct | BIGINT | Nullable |
| utilization_pct | FLOAT | Nullable |
| total_flight_hours | FLOAT | Non-nullable, Default `0.0` (Backfill từ giờ bay của các mission đã hoàn thành) |
| last_maintenance_at | TIMESTAMP | Nullable |

> **Quy tắc chuẩn hóa tên hiển thị Drone (drones.name):**
> - **Light (<= 1.0kg - DJI Matrice 100 / Mã ID `M100`)**: Swift XX (Swift 01 - Swift 72)
> - **Medium (<= 5.0kg - Aurelia X6 Pro / Mã ID `X6P`)**: Falcon XX (Falcon 01 - Falcon 57)
> - **Heavy (> 5.0kg - DJI FlyCart 15 Heavy / Mã ID `FC15`)**: Eagle XX (Eagle 01 - Eagle 56)

### 7. Bảng hubs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| code | VARCHAR | Nullable |
| name | VARCHAR | Nullable |
| address | VARCHAR | Nullable |
| latitude | FLOAT | Nullable |
| longitude | FLOAT | Nullable |
| status | VARCHAR | Nullable |
| created_at | TIMESTAMP | Nullable |
| updated_at | TIMESTAMP | Nullable |

### 8. Bảng hub_supplies (đổi tên từ hub_inventories)
Số lượng linh kiện / vật tư tại từng Hub. Không có kho trung tâm — mỗi Hub quản lý vật tư riêng.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Non-nullable |
| quantity | INTEGER | Non-nullable, Default `0` |
| min_stock_threshold | INTEGER | Non-nullable, Default `2` |
| updated_at | TIMESTAMPTZ | Nullable |

> Unique: `uq_hub_supply_part` trên `(hub_id, part_category_id)`.

### 9. Bảng incidents
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Nullable |
| drone_id | VARCHAR(50) | Nullable |
| reporter_id | VARCHAR | Nullable |
| severity | VARCHAR(20) | Non-nullable (`risk_level`) |
| description | VARCHAR / TEXT | Non-nullable |
| status | VARCHAR(20) | Non-nullable (`incidents_status`) |
| reported_at | TIMESTAMPTZ | Nullable |
| requires_technical_inspection | BOOLEAN | Non-nullable, Default `false` |

### 10. Bảng maintenance_alerts
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| source | VARCHAR | Nullable |
| maintenance_schedule_id | INTEGER | Nullable |
| mission_id | VARCHAR | Nullable |
| incident_id | INTEGER | Nullable |
| title | VARCHAR | Nullable |
| description | VARCHAR(1000) | Nullable (Chi tiết đầy đủ cho UI: Mission/Order ID, Incident details, Issue description) |
| status | VARCHAR | Nullable |
| work_order_id | INTEGER | Nullable |
| handled_by | VARCHAR | Nullable |
| created_at | TIMESTAMP | Nullable |
| handled_at | TIMESTAMP | Nullable |

### 11. Bảng maintenance_schedules
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| maintenance_type | VARCHAR | Nullable |
| interval_days | INTEGER | Nullable |
| interval_flight_hours | FLOAT | Nullable |
| last_inspection_at | TIMESTAMP | Nullable |
| next_inspection_at | TIMESTAMP | Nullable |
| status | VARCHAR | Nullable |
| created_at | TIMESTAMP | Nullable |
| updated_at | TIMESTAMP | Nullable |

### 12. Bảng mission_legs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Nullable |
| sequence_no | INTEGER | Nullable |
| from_hub_id | BIGINT | Nullable |
| to_hub_id | BIGINT | Nullable |
| battery_id | VARCHAR(50) | **FK** (`batteries.id`), Nullable |
| status | VARCHAR(20) | Nullable (`mission_leg_status_enum`) |
| started_at | TIMESTAMP | Nullable |
| completed_at | TIMESTAMP | Nullable |
| created_at | TIMESTAMP | Nullable |
| updated_at | TIMESTAMP | Nullable |

### 13. Bảng missions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable |
| order_id | VARCHAR(50) | Nullable |
| customer_id | VARCHAR | **FK** (`accounts.id`), Nullable |
| operator_id | VARCHAR | Nullable |
| drone_id | VARCHAR(50) | **FK** (`drones.id`), Nullable |
| battery_id | VARCHAR(50) | **FK** (`batteries.id`), Nullable |
| payload_weight | FLOAT | Nullable |
| distance_m | FLOAT | Nullable |
| delivery_fee | FLOAT | Nullable |
| status | VARCHAR(50) | Non-nullable (`missions_status`: `Scheduled`, `Flying`, `Arrival completed`, `Mission completed`, `Incident return`, `Failed`, `Cancelled`) |
| handling_status | VARCHAR(50) | Nullable (`missions_handling_status`) |
| scheduled_time | TIMESTAMP | Nullable |
| start_time | TIMESTAMP | Nullable |
| end_time | TIMESTAMP | Nullable |
| origin_hub_id | BIGINT | Nullable |
| destination_hub_id | BIGINT | Nullable |
| departed_at | TIMESTAMP | Nullable |
| arrived_at | TIMESTAMP | Nullable |
| arrival_confirmed_by | VARCHAR | Nullable |
| arrival_confirmed_at | TIMESTAMP | Nullable |
| battery_charge_at_arrival | FLOAT | Nullable (Mức pin % lúc drone hạ cánh hub) |
| leg_destination_type | VARCHAR(20) | Nullable, Default `'destination'` |
| checkpoints_json | JSONB | Nullable (Hợp nhất từ mission_hub_checkpoints) |

### 14. Bảng notifications
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| user_id | VARCHAR | Non-nullable |
| title | VARCHAR(255) | Non-nullable |
| message | TEXT | Non-nullable |
| notifications_type | VARCHAR(50) | Non-nullable (`notifications_type`) |
| reference_id | VARCHAR | Nullable |
| is_read | BOOLEAN | Non-nullable, Default `false` |
| created_at | TIMESTAMPTZ | Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| work_order_id | INTEGER | Nullable |
| mission_id | VARCHAR | Nullable |

### 15. Bảng orders
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable |
| customer_id | VARCHAR(50) | Nullable |
| sender_phone | VARCHAR(20) | Nullable |
| sender_email | VARCHAR(255) | Nullable |
| receiver_name | VARCHAR(100) | Nullable |
| receiver_phone | VARCHAR(20) | Nullable |
| package_label | VARCHAR(255) | Nullable |
| package_type | VARCHAR(100) | Nullable |
| payload_kg | FLOAT | Nullable |
| origin_hub_id | INTEGER | **FK** (`hubs.id`), Nullable |
| destination_hub_id | BIGINT | Nullable |
| delivery_mode | VARCHAR(20) | Nullable (`delivery_mode_enum`: EXPRESS, SCHEDULED, NORMAL) |
| requested_delivery_at | TIMESTAMP | Nullable |
| estimated_window_start | TIMESTAMP | Nullable |
| estimated_window_end | TIMESTAMP | Nullable |
| planning_at | TIMESTAMP | Nullable |
| status | VARCHAR(50) | Nullable (`orders_status_enum`) |
| origin_received_at | TIMESTAMP | Nullable |
| origin_received_by | VARCHAR(50) | Nullable |
| destination_received_at | TIMESTAMP | Nullable |
| destination_received_by | VARCHAR(50) | Nullable |
| replan_required_at | TIMESTAMP | Nullable |
| created_at | TIMESTAMP | Nullable |
| updated_at | TIMESTAMP | Nullable |
| cancelled_at | TIMESTAMP | Nullable |
| delivery_fee | FLOAT | Nullable |
| receiver_email | VARCHAR(255) | Nullable |
| payment_status | VARCHAR(20) | Nullable (`payment_status_enum`) |
| payment_order_code | BIGINT | Unique, Nullable |
| payment_transaction_id | VARCHAR(100) | Nullable |
| payment_checkout_url | VARCHAR(500) | Nullable |
| payment_qr_code | VARCHAR(1000) | Nullable |
| payment_bin | VARCHAR(20) | Nullable |
| payment_account_number | VARCHAR(50) | Nullable |
| payment_account_name | VARCHAR(100) | Nullable |
| paid_at | TIMESTAMP | Nullable |
| is_aging_priority | BOOLEAN | Non-nullable, Default `false` |
| aging_deadline | TIMESTAMP | Nullable |
| refund_at | TIMESTAMP | Nullable |
| refund_amount | FLOAT | Nullable |
| refund_reason | VARCHAR(255) | Nullable |
| refund_status | VARCHAR(50) | Nullable (`refund_status_enum`), Default `'PENDING'` |
| cancellation_reason | VARCHAR(500) | Nullable |

### 16. Bảng part_categories
Danh mục loại linh kiện / vật tư — dùng chung toàn hệ thống.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| name | VARCHAR(100) | Unique, Non-nullable |
| category_type | VARCHAR(50) | Non-nullable (`part_category_type_enum`) |
| compatible_drone_models | JSONB | Nullable |
| unit_cost | FLOAT | Nullable (VNĐ / đơn vị) |
| description | TEXT | Nullable |
| is_active | BOOLEAN | Non-nullable, Default `true` |
| created_at | TIMESTAMPTZ | Nullable |

### 17. Bảng parts_requests
Technician đề xuất sử dụng linh kiện để sửa chữa drone. Admin duyệt hoặc điều phối.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| requested_by | VARCHAR | Non-nullable (user_id Technician) |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Non-nullable |
| quantity_needed | INTEGER | Non-nullable, Default `1` |
| description | TEXT | Non-nullable |
| photo_urls | JSONB | Nullable |
| status | VARCHAR(20) | Non-nullable (`parts_request_status_enum`) |
| reviewed_by | VARCHAR | Nullable |
| admin_note | TEXT | Nullable |
| reviewed_at | TIMESTAMPTZ | Nullable |
| actual_cost | FLOAT | Nullable |
| created_at | TIMESTAMPTZ | Non-nullable |
| fulfilled_at | TIMESTAMPTZ | Nullable |

### 18. Bảng supply_transactions (đổi tên từ inventory_transactions)
Log mọi biến động vật tư tại Hub. `quantity_delta > 0` = nhập, `< 0` = xuất.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Non-nullable |
| quantity_delta | INTEGER | Non-nullable (+/−) |
| reason | VARCHAR(20) | Non-nullable (`supply_txn_reason_enum`: Receive, RepairUse, TransferIn, TransferOut, Adjust, Discard) |
| performed_by | VARCHAR | Nullable |
| reference_id | VARCHAR | Nullable |
| note | TEXT | Nullable |
| created_at | TIMESTAMPTZ | Non-nullable |

### 19. Bảng system_logs
Ghi lại sự kiện tự động của hệ thống: background workers, cron jobs, lỗi nền.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| source | VARCHAR(100) | Non-nullable |
| level | VARCHAR(20) | Non-nullable, Default `'INFO'` |
| event | VARCHAR(100) | Non-nullable |
| message | TEXT | Non-nullable |
| details_json | JSONB | Nullable |
| created_at | TIMESTAMPTZ | Non-nullable, Default `now()` |

### 20. Bảng telemetry_logs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| timestamp | TIMESTAMP | Non-nullable |
| latitude | FLOAT | Non-nullable |
| longitude | FLOAT | Non-nullable |
| altitude | FLOAT | Non-nullable |
| speed | FLOAT | Non-nullable |
| battery_voltage | FLOAT | Nullable |
| battery_pct | FLOAT | Nullable (Dung lượng pin thực tế 0-100% đồng bộ Telemetry) |
| energy_consumed_wh | FLOAT | Nullable |
| wind_speed | FLOAT | Nullable |
| weather_temperature | FLOAT | Nullable |
| weather_apparent_temp | FLOAT | Nullable |
| weather_dew_point | FLOAT | Nullable |
| weather_humidity | FLOAT | Nullable |
| weather_wind_speed | FLOAT | Nullable |
| weather_wind_gust | FLOAT | Nullable |
| weather_wind_direction | FLOAT | Nullable |
| weather_precipitation | FLOAT | Nullable |
| weather_pressure | FLOAT | Nullable |
| weather_cloud_cover | FLOAT | Nullable |

### 21. Bảng work_orders
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | Non-nullable (Chuẩn `DRN-XXXX`) |
| battery_id | VARCHAR(50) | Nullable |
| technician_id | VARCHAR | Non-nullable |
| issue_description | TEXT | Non-nullable |
| action_taken | TEXT | Nullable |
| status | VARCHAR(20) | Non-nullable (`work_orders_status`) |
| resolved_at | TIMESTAMPTZ | Nullable |
| alert_id | BIGINT | Nullable |
| title | VARCHAR | Nullable |
| priority | VARCHAR | Nullable |
| created_by | VARCHAR | Nullable |
| assigned_to | VARCHAR | Nullable |
| scheduled_at | TIMESTAMP | Nullable |
| started_at | TIMESTAMP | Nullable |
| completed_at | TIMESTAMP | Nullable |
| diagnosis | TEXT | Nullable (Hợp nhất từ maintenance_records) |
| resulting_drone_status | VARCHAR(50) | Nullable (Hợp nhất từ maintenance_records) |
| record_completed_at | TIMESTAMP | Nullable (Hợp nhất từ maintenance_records) |
| inspection_items_json | JSONB | Nullable, Default `'[]'` (Hợp nhất từ maintenance_inspection_items) |
| status_logs_json | JSONB | Nullable, Default `'[]'` (Hợp nhất từ work_order_logs) |
| created_at | TIMESTAMPTZ | Nullable, Default `now()` |

### 22. Bảng replacement_requests
Yêu cầu thay thế pin hoặc linh kiện từ Technician (được Admin cấp phát hoặc từ chối).

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGSERIAL | **PK**, Non-nullable |
| requested_by | VARCHAR | Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| type | VARCHAR | Non-nullable (`replacement_type_enum`: BATTERY, PART; `create_type=False`) |
| damaged_item_id | VARCHAR | Nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Nullable |
| quantity | INTEGER | Non-nullable, Default `1` |
| reason | TEXT | Non-nullable |
| photo_urls | JSONB | Nullable |
| status | VARCHAR | Non-nullable (`replacement_status_enum`: AWAITING_SUPPLY, SUPPLIED, REJECTED; `create_type=False`) |
| reviewed_by | VARCHAR | Nullable |
| reviewed_at | TIMESTAMPTZ | Nullable |
| rejection_reason | TEXT | Nullable |
| supplied_item_id | VARCHAR | Nullable |
| supplied_at | TIMESTAMPTZ | Nullable |
| created_at | TIMESTAMPTZ | Non-nullable |

### 23. Bảng delivery_ratings
Đánh giá trải nghiệm giao hàng của Customer sau khi đơn hàng hoàn tất. Mỗi đơn hàng chỉ được đánh giá 1 lần.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGSERIAL | **PK**, Non-nullable |
| order_id | VARCHAR(50) | **FK** (`orders.id`), Unique, Non-nullable |
| customer_id | VARCHAR(50) | **FK** (`accounts.id`), Non-nullable |
| rating | INTEGER | Non-nullable (`CHECK (rating BETWEEN 1 AND 5)`) |
| comment | VARCHAR(1000) | Nullable |
| created_at | TIMESTAMP | Non-nullable |

> Indexes: `idx_delivery_ratings_order (order_id)`, `idx_delivery_ratings_customer (customer_id)`, `idx_delivery_ratings_rating (rating)`.

### 24. Bảng saved_addresses
Danh bạ địa chỉ giao hàng của Customer — lưu thông tin người nhận thường dùng.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGSERIAL | **PK**, Non-nullable |
| customer_id | VARCHAR(50) | **FK** (`accounts.id`), Non-nullable |
| label | VARCHAR(100) | Non-nullable |
| recipient_name | VARCHAR(100) | Non-nullable |
| recipient_phone | VARCHAR(20) | Non-nullable |
| recipient_email | VARCHAR(255) | Nullable |
| hub_id | BIGINT | **FK** (`hubs.id`), Nullable |
| is_default | BOOLEAN | Non-nullable, Default `false` |
| created_at | TIMESTAMP | Non-nullable |

> Index: `idx_saved_addresses_customer (customer_id)`.

### 25. Bảng drone_daily_stats
Thống kê hoạt động drone theo ngày — được tổng hợp cuối mỗi ngày bởi worker.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGSERIAL | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | **FK** (`drones.id`), Non-nullable |
| stat_date | TIMESTAMP | Non-nullable |
| mission_count | INTEGER | Non-nullable, Default `0` |
| completed_count | INTEGER | Non-nullable, Default `0` |
| incident_count | INTEGER | Non-nullable, Default `0` |
| total_distance_km | FLOAT | Non-nullable, Default `0.0` |
| total_flight_min | FLOAT | Non-nullable, Default `0.0` |
| total_energy_wh | FLOAT | Non-nullable, Default `0.0` |
| created_at | TIMESTAMP | Non-nullable |

> Index: `idx_drone_daily_drone_date (drone_id, stat_date)`.

---

## II. Lịch sử Migration & Quản lý Backup

### 1. Bản Snapshot Toàn diện & Script Khôi phục (Undo / Rollback)
- **File Snapshot Backup (trước khi Clean & Reset DB)**: `app/shared/database/backup_before_clean_reset.json` (~4.99 MB, sao lưu toàn bộ dữ liệu thực tế của các bảng trước khi Clean & Reset).
- **Script Rollback (Clean & Reset)**: `app/shared/database/rollback_clean_reset.py` (sẵn sàng khôi phục 100% dữ liệu trước khi Clean & Reset chỉ với một lệnh).
- **File Snapshot Backup (Core Schema V3)**: `app/shared/database/backup_full_db_snapshot.json` (3.14 MB, sao lưu cấu trúc và dữ liệu đầy đủ của 33 bảng và 19 ENUM).
- **Script Rollback (Core Schema V3)**: `app/shared/database/undo_core_schema_backup.py`.

### 2. Migration Core Schema V3 & V5
- **Script Thực thi**:
  - `app/shared/database/migrate_core_schema_v3.py`: Đổi tên `users` thành `accounts`, hợp nhất 14 bảng phụ về 19 bảng lõi.
  - `app/shared/database/migrate_rename_inventory_to_supplies.py`: Đổi tên `hub_inventories` $\rightarrow$ `hub_supplies` và `inventory_transactions` $\rightarrow$ `supply_transactions`.
  - `app/shared/database/migrate_logs_schema.py`: Tách `audit_logs` thành `activity_logs` (user actions) và `system_logs` (cron/background events).
  - `app/shared/database/migrate_battery_catalog.py`: Thêm bảng `battery_model_catalog` (seed 3 model pin chuẩn: `TB48D` 130Wh, `Aurelia UltraPower` 444Wh, `DJI DB2000` 980Wh).
  - `app/shared/database/migrate_replacement_requests.py`: Thêm bảng `replacement_requests` và đồng bộ `orders.refund_status` (Chuẩn hóa **22 bảng chính thức**, không còn cột `orders.sender_email`).
  - Chuẩn hóa ID & Model Pin toàn hệ thống: `drones.id` → `{M100|X6P|FC15}-DRNXXXX`, `batteries.id` → `{TB48|UP12|DB20}-BATXXXX`, chuẩn hóa `drones.name` (`Swift/Falcon/Eagle`), `serial_number` (`SN-DRN-XXXX`/`SN-BAT-XXXX`), đồng bộ `battery_model` & `battery_model_catalog` (`DJI TB48D`, `Aurelia UltraPower 12S`, `DJI DB2000`), bật `ON UPDATE CASCADE` cho `missions_battery_id_fkey` và `mission_legs_battery_id_fkey`, và đồng bộ toàn bộ cột tham chiếu/nhật ký liên quan.

### 3. Performance Indexes (Done-Backend-v8)
- Đã tạo 25 non-blocking indexes (`CREATE INDEX CONCURRENTLY IF NOT EXISTS`) trên các bảng nóng và đồng bộ vào SQLAlchemy models:
  - `telemetry_logs`: `idx_telemetry_logs_mission_ts (mission_id, timestamp DESC)`, `idx_telemetry_logs_mission_id_desc (mission_id, id DESC)`
  - `notifications`: `idx_notifications_user_created (user_id, created_at DESC)`, `idx_notifications_user_unread (user_id) WHERE is_read = false`, `idx_notifications_ref_created (reference_id, created_at DESC)`
  - `missions`: `idx_missions_order_id_desc (order_id, id DESC)`, `idx_missions_status (status)`, `idx_missions_drone_id (drone_id)`, `idx_missions_origin_hub (origin_hub_id)`
  - `mission_legs`: `idx_mission_legs_mission_seq (mission_id, sequence_no)`, `idx_mission_legs_to_hub_status (to_hub_id, status)`
  - `orders`: `idx_orders_customer_created (customer_id, created_at DESC)`, `idx_orders_status_created (status, created_at DESC)`, `idx_orders_origin_hub_status (origin_hub_id, status)`, `idx_orders_payment_status (payment_status)`
  - `ai_predictions`: `idx_ai_predictions_mission_id_desc (mission_id, id DESC)`
  - `batteries`: `idx_batteries_hub_status (current_hub_id, status)`, `idx_batteries_drone_id (drone_id)`
  - `drones`: `idx_drones_status_hub (operational_status, current_hub_id)`
  - `work_orders`: `idx_work_orders_drone_status (drone_id, status)`, `idx_work_orders_tech_status (technician_id, status)`
  - `maintenance_alerts`: `idx_maint_alerts_drone_status (drone_id, status)`, `idx_maint_alerts_mission_id (mission_id)`
  - `incidents`: `idx_incidents_mission_status (mission_id, status)`
  - `accounts`: `idx_accounts_role_id (role_id)`
