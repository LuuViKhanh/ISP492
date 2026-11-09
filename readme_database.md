# Database Schema Documentation
Đây là tài liệu Single Source of Truth cho cấu trúc cơ sở dữ liệu của dự án Drone-backend, được tự động cập nhật và đồng bộ theo models và các bản migration mới nhất (tuân thủ quy định `GEMINI.md`).

> **Kiến trúc Cơ sở dữ liệu Chuẩn hóa (Database Schema - 21 Bảng)**:
> Hệ thống gồm **21 bảng chính thức** lưu trữ và điều phối toàn bộ chu trình vận hành bay, phân phối trạm, bảo trì, quản lý pin, vật tư linh kiện, đơn hàng và nhật ký hệ thống.
> *(Các bảng cũ `audit_logs`, `hub_inventories`, `inventory_transactions` đã được thay thế/đổi tên hoàn toàn sang `activity_logs`, `system_logs`, `hub_supplies`, `supply_transactions` và xóa khỏi Database).*

> **Lưu ý về Múi giờ (Timezone)**: 
> Toàn bộ các trường kiểu thời gian (`DATETIME`, `TIMESTAMP`, `TIMESTAMPTZ`) trong hệ thống được quản lý, khởi tạo và ghi nhận đồng bộ theo múi giờ chuẩn Việt Nam (GMT+7 / `Asia/Ho_Chi_Minh`) thông qua module `app.core.timezone`.

> **Lưu ý về Định dạng Mã Drone (Drone ID Format)**:
> Hệ thống sử dụng định dạng mã chuỗi chuẩn `DRN-XXXX` (ví dụ `DRN-0001`, `DRN-0002`). Toàn bộ khóa chính `drones.id` và các khóa ngoại/tham chiếu `drone_id` ở các bảng liên quan đều sử dụng kiểu `VARCHAR(50)`.

---

## I. Cấu trúc 21 Bảng Hệ Thống (Database Tables)

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
| id | VARCHAR(50) | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | **FK** (`drones.id`), Nullable |
| serial_number | VARCHAR(100) | Unique, Non-nullable |
| capacity_wh | FLOAT | Non-nullable |
| status | VARCHAR(20) | Non-nullable (`batteries_status`) |
| current_hub_id | INTEGER | **FK** (`hubs.id`), Nullable |
| charge_level_pct | BIGINT | Nullable |
| create_at | TIMESTAMPTZ | Nullable |
| update_at | TIMESTAMPTZ | Nullable |
| battery_model | VARCHAR(100) | Nullable |
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
| id | VARCHAR(50) | **PK**, Non-nullable (Chuẩn `DRN-XXXX`) |
| name | VARCHAR(100) | Non-nullable |
| model | VARCHAR(100) | Non-nullable |
| payload_capacity_kg | FLOAT | Non-nullable |
| max_speed | FLOAT | Non-nullable |
| operational_status | VARCHAR(20) | Non-nullable (`drone_status`) |
| current_hub_id | BIGINT | Nullable |
| battery_level_pct | BIGINT | Nullable |
| utilization_pct | FLOAT | Nullable |

> **Quy tắc chuẩn hóa tên hiển thị Drone (drones.name):**
> - **Light (<= 1.0kg - DJI Matrice 100)**: Swift XX (Swift 01 - Swift 40)
> - **Medium (<= 5.0kg - Aurelia X6 Pro)**: Falcon XX (Falcon 01 - Falcon 33)
> - **Heavy (> 5.0kg - DJI FlyCart 15 Heavy)**: Eagle XX (Eagle 01 - Eagle 32)

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
| status | VARCHAR(50) | Non-nullable (`missions_status`) |
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
  - Chuẩn hóa Database còn chính xác **21 bảng chính thức** hoạt động ổn định và tối ưu hiệu năng.
