# Database Schema Documentation
Đây là tài liệu Single Source of Truth cho cấu trúc cơ sở dữ liệu của dự án Drone-backend, được tự động cập nhật và đồng bộ theo models và các bản migration mới nhất (tuân thủ quy định `GEMINI.md`).

> **Kiến trúc Cơ sở dữ liệu Tinh gọn (Core Schema - 19 Bảng Lõi)**:
> Hệ thống đã được chuẩn hóa và loại bỏ các bảng/thuộc tính dư thừa, hợp nhất dữ liệu phi cấu trúc vào JSONB, đổi tên bảng `users` thành `accounts` (đồng thời duy trì alias `User = Account` để code backend tương thích 100%).

> **Lưu ý về Múi giờ (Timezone)**: 
> Toàn bộ các trường kiểu thời gian (`DATETIME`, `TIMESTAMP`) trong hệ thống được quản lý, khởi tạo và ghi nhận đồng bộ theo múi giờ chuẩn Việt Nam (GMT+7 / `Asia/Ho_Chi_Minh`) thông qua module `app.core.timezone`.

> **Lưu ý về Định dạng Mã Drone (Drone ID Format)**:
> Hệ thống sử dụng định dạng mã chuỗi chuẩn `DRN-XXXX` (ví dụ `DRN-0001`, `DRN-0002`). Toàn bộ khóa chính `drones.id` và các khóa ngoại/tham chiếu `drone_id` ở các bảng liên quan đều sử dụng kiểu `VARCHAR(50)`.

---

## I. Cấu trúc 19 Bảng Lõi (Core Tables)

### 1. Bảng accounts (trước đây là users)
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable |
| role_id | VARCHAR | Nullable |
| password_hash | VARCHAR | Nullable |
| full_name | VARCHAR | Non-nullable |
| email | VARCHAR | Unique, Non-nullable |
| is_active | BOOLEAN | Non-nullable, Default `true` |
| created_at | DATETIME | Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Nullable |
| reset_token | VARCHAR(255) | Nullable (Hợp nhất từ password_reset_tokens) |
| reset_token_expires_at | TIMESTAMP WITH TIME ZONE | Nullable |

### 2. Bảng ai_predictions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| estimated_energy_wh | FLOAT | Non-nullable |
| estimated_duration_m | FLOAT | Non-nullable |
| risk_level | VARCHAR(20) | Non-nullable |
| recommendation_content | TEXT | Nullable (Hợp nhất từ ai_recommendations) |
| is_recommendation_applied | BOOLEAN | Nullable, Default `false` |
| predicted_at | DATETIME | Non-nullable |

### 3. Bảng audit_logs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| user_id | VARCHAR | Nullable |
| action | VARCHAR | Non-nullable |
| resource_table | VARCHAR | Nullable |
| details_json | JSON | Nullable |
| created_at | DATETIME | Non-nullable |

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
| create_at | DATETIME | Nullable |
| update_at | DATETIME | Nullable |
| battery_model | VARCHAR(100) | Nullable |
| last_charged_at | DATETIME | Nullable |

### 5. Bảng drones
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

### 6. Bảng hubs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| code | VARCHAR | Nullable |
| name | VARCHAR | Nullable |
| address | VARCHAR | Nullable |
| latitude | FLOAT | Nullable |
| longitude | FLOAT | Nullable |
| status | VARCHAR | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |

### 7. Bảng hub_inventories
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Non-nullable |
| quantity | INTEGER | Non-nullable, Default `0` |
| min_stock_threshold | INTEGER | Non-nullable, Default `2` |
| updated_at | DATETIME | Nullable |

### 8. Bảng incidents
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Nullable |
| drone_id | VARCHAR(50) | Nullable |
| reporter_id | VARCHAR | Nullable |
| severity | VARCHAR(20) | Non-nullable (`risk_level`) |
| description | TEXT | Non-nullable |
| status | VARCHAR(20) | Non-nullable (`incidents_status`) |
| reported_at | TIMESTAMP WITH TIME ZONE | Nullable |
| requires_technical_inspection | BOOLEAN | Non-nullable, Default `false` |

### 9. Bảng inventory_transactions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Non-nullable |
| quantity_delta | INTEGER | Non-nullable |
| reason | VARCHAR(50) | Non-nullable (`inventory_transaction_reason_enum`) |
| performed_by | VARCHAR | Nullable |
| reference_id | VARCHAR | Nullable |
| note | TEXT | Nullable |
| created_at | DATETIME | Nullable |

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
| created_at | DATETIME | Nullable |
| handled_at | DATETIME | Nullable |

### 11. Bảng maintenance_schedules
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| maintenance_type | VARCHAR | Nullable |
| interval_days | INTEGER | Nullable |
| interval_flight_hours | FLOAT | Nullable |
| last_inspection_at | DATETIME | Nullable |
| next_inspection_at | DATETIME | Nullable |
| status | VARCHAR | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |

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
| started_at | DATETIME | Nullable |
| completed_at | DATETIME | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |

### 13. Bảng missions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable |
| order_id | VARCHAR(50) | Nullable |
| customer_id | VARCHAR | Nullable |
| operator_id | VARCHAR | Nullable |
| drone_id | VARCHAR(50) | Nullable |
| battery_id | VARCHAR(50) | Nullable |
| payload_weight | FLOAT | Nullable |
| distance_m | FLOAT | Nullable |
| delivery_fee | FLOAT | Nullable |
| status | VARCHAR(50) | Non-nullable (`missions_status`) |
| handling_status | VARCHAR(50) | Nullable (`missions_handling_status`) |
| scheduled_time | DATETIME | Nullable |
| start_time | DATETIME | Nullable |
| end_time | DATETIME | Nullable |
| origin_hub_id | BIGINT | Nullable |
| destination_hub_id | BIGINT | Nullable |
| departed_at | DATETIME | Nullable |
| arrived_at | DATETIME | Nullable |
| arrival_confirmed_by | VARCHAR | Nullable |
| arrival_confirmed_at | DATETIME | Nullable |
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
| created_at | DATETIME | Non-nullable |
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
| origin_hub_id | BIGINT | Nullable |
| destination_hub_id | BIGINT | Nullable |
| delivery_mode | VARCHAR(20) | Nullable (`delivery_mode_enum`: EXPRESS, SCHEDULED, NORMAL) |
| requested_delivery_at | DATETIME | Nullable |
| estimated_window_start | DATETIME | Nullable |
| estimated_window_end | DATETIME | Nullable |
| planning_at | DATETIME | Nullable |
| status | VARCHAR(50) | Nullable (`orders_status_enum`) |
| origin_received_at | DATETIME | Nullable |
| origin_received_by | VARCHAR(50) | Nullable |
| destination_received_at | DATETIME | Nullable |
| destination_received_by | VARCHAR(50) | Nullable |
| replan_required_at | DATETIME | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |
| cancelled_at | DATETIME | Nullable |
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
| paid_at | DATETIME | Nullable |
| is_aging_priority | BOOLEAN | Non-nullable, Default `false` |
| aging_deadline | DATETIME | Nullable |

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

### 20. Bảng hub_supplies *(đổi tên từ hub_inventories)*
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

### 21. Bảng supply_transactions *(đổi tên từ inventory_transactions)*
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

### 22. Bảng battery_model_catalog
Catalog model pin với giá và danh sách drone tương thích.

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

### 17. Bảng parts_requests
Technician đề xuất sử dụng linh kiện để sửa chữa drone. Admin duyệt hoặc điều phối.

| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| requested_by | VARCHAR | Non-nullable (user_id Technician) |
| hub_id | INTEGER | **FK** (`hubs.id`), Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| part_category_id | BIGINT | **FK** (`part_categories.id`), Non-nullable |
| quantity_needed | INTEGER | Non-nullable |
| description | TEXT | Non-nullable |
| photo_urls | JSONB | Nullable |
| status | VARCHAR(20) | Non-nullable (`parts_request_status_enum`) |
| reviewed_by | VARCHAR | Nullable |
| admin_note | TEXT | Nullable |
| reviewed_at | TIMESTAMPTZ | Nullable |
| actual_cost | FLOAT | Nullable |
| created_at | TIMESTAMPTZ | Non-nullable |
| fulfilled_at | TIMESTAMPTZ | Nullable |

### 18. Bảng telemetry_logs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| timestamp | DATETIME | Non-nullable |
| latitude | FLOAT | Non-nullable |
| longitude | FLOAT | Non-nullable |
| altitude | FLOAT | Non-nullable |
| speed | FLOAT | Non-nullable |
| battery_voltage | FLOAT | Nullable |
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

### 19. Bảng work_orders
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | Nullable (Chuẩn `DRN-XXXX`) |
| battery_id | VARCHAR(50) | Nullable |
| technician_id | VARCHAR | Non-nullable |
| issue_description | TEXT | Non-nullable |
| action_taken | TEXT | Nullable |
| status | VARCHAR(20) | Non-nullable (`work_orders_status`) |
| resolved_at | TIMESTAMP WITH TIME ZONE | Nullable |
| alert_id | BIGINT | Nullable |
| title | VARCHAR | Nullable |
| priority | VARCHAR | Nullable |
| created_by | VARCHAR | Nullable |
| assigned_to | VARCHAR | Nullable |
| scheduled_at | DATETIME | Nullable |
| started_at | DATETIME | Nullable |
| completed_at | DATETIME | Nullable |
| diagnosis | TEXT | Nullable (Hợp nhất từ maintenance_records) |
| resulting_drone_status | VARCHAR(50) | Nullable (Hợp nhất từ maintenance_records) |
| record_completed_at | DATETIME | Nullable (Hợp nhất từ maintenance_records) |
| inspection_items_json | JSONB | Nullable (Hợp nhất từ maintenance_inspection_items) |
| status_logs_json | JSONB | Nullable (Hợp nhất từ work_order_logs) |

---

## II. Lịch sử Migration & Quản lý Backup

### 1. Bản Snapshot Toàn diện & Script Khôi phục (Undo)
- **File Snapshot Backup**: `app/shared/database/backup_full_db_snapshot.json` (3.14 MB, sao lưu cấu trúc và dữ liệu đầy đủ của 33 bảng và 19 ENUM).
- **Script Rollback**: `app/shared/database/undo_core_schema_backup.py` (sẵn sàng khôi phục nguyên trạng 33 bảng chỉ với một lệnh khi cần).

### 2. Migration Core Schema V3
- **Script Thực thi**: `app/shared/database/migrate_core_schema_v3.py`
  - Đổi tên `users` thành `accounts`.
  - Hợp nhất dữ liệu từ 14 bảng phụ (`ai_recommendations`, `maintenance_records`, `maintenance_inspection_items`, `work_order_logs`, `mission_hub_checkpoints`, `password_reset_tokens`, v.v.) vào các cột chuyên dụng và JSONB trên các bảng lõi.
  - Xóa an toàn các bảng phụ và cột không còn sử dụng.
  - Chuẩn hóa Database còn chính xác 19 bảng lõi hoạt động ổn định và tối ưu hiệu năng.
