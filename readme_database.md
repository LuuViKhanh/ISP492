# Database Schema Documentation
Đây là tài liệu Single Source of Truth cho cấu trúc cơ sở dữ liệu của dự án Drone-backend, được tự động cập nhật và đồng bộ theo models và các bản migration mới nhất (tuân thủ quy định `GEMINI.md`).

> **Lưu ý về Múi giờ (Timezone)**: 
> Toàn bộ các trường kiểu thời gian (`DATETIME`, `TIMESTAMP`) trong hệ thống được quản lý, khởi tạo và ghi nhận đồng bộ theo múi giờ chuẩn Việt Nam (GMT+7 / `Asia/Ho_Chi_Minh`) thông qua module `app.core.timezone`.

> **Lưu ý về Định dạng Mã Drone (Drone ID Format)**:
> Hệ thống sử dụng định dạng mã chuỗi chuẩn `DRN-XXXX` (ví dụ `DRN-0001`, `DRN-0002`). Toàn bộ khóa chính `drones.id` và các khóa ngoại/tham chiếu `drone_id` ở các bảng liên quan đều sử dụng kiểu `VARCHAR(50)`.

---

## I. Cấu trúc các Bảng (Tables)

### Bảng ai_predictions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| estimated_energy_wh | FLOAT | Non-nullable |
| estimated_duration_m | FLOAT | Non-nullable |
| risk_level | VARCHAR(20) | Non-nullable |
| shap_values_json | JSONB | Nullable |
| predicted_at | DATETIME | Non-nullable |

### Bảng ai_recommendations
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| category | VARCHAR(50) | Non-nullable |
| content | TEXT | Non-nullable |
| is_applied | BOOLEAN | Non-nullable |
| created_at | DATETIME | Non-nullable |

### Bảng audit_logs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| user_id | VARCHAR | Nullable |
| action | VARCHAR | Non-nullable |
| resource_table | VARCHAR | Nullable |
| details_json | JSON | Nullable |
| created_at | DATETIME | Non-nullable |

### Bảng batteries
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

### Bảng drones
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

### Bảng hubs
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

### Bảng incidents
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Nullable |
| drone_id | VARCHAR(50) | Nullable |
| reporter_id | VARCHAR | Nullable |
| severity | VARCHAR(20) | Non-nullable (`risk_level`) |
| description | VARCHAR | Non-nullable |
| status | VARCHAR(20) | Non-nullable (`incidents_status`) |
| reported_at | DATETIME | Nullable |
| requires_technical_inspection | BOOLEAN | Non-nullable |

### Bảng locations
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| name | VARCHAR | Non-nullable |
| latitude | FLOAT | Non-nullable |
| longitude | FLOAT | Non-nullable |
| type | VARCHAR(20) | Non-nullable |

### Bảng maintenance_alerts
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
| acknowledged_by | VARCHAR(50) | Nullable (Có script migration) |
| acknowledged_at | DATETIME | Nullable (Có script migration) |

### Bảng maintenance_inspection_items
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| maintenance_record_id | INTEGER | Nullable |
| item_name | VARCHAR | Nullable |
| is_completed | BOOLEAN | Nullable |
| checked_at | DATETIME | Nullable |

### Bảng maintenance_records
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| work_order_id | INTEGER | Nullable |
| drone_id | VARCHAR(50) | Nullable |
| performed_by | VARCHAR | Nullable |
| title | VARCHAR | Nullable |
| diagnosis | TEXT | Nullable |
| corrective_action | TEXT | Nullable |
| resulting_drone_status | VARCHAR | Nullable |
| created_at | DATETIME | Nullable |
| completed_at | DATETIME | Nullable |

### Bảng maintenance_schedules
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

### Bảng mission_battery_swap_history
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | **FK**, Nullable |
| hub_id | INTEGER | **FK**, Nullable |
| old_battery_id | VARCHAR(50) | **FK**, Nullable |
| new_battery_id | VARCHAR(50) | **FK**, Nullable |
| swapped_at | DATETIME | Nullable |

### Bảng mission_hub_checkpoints
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| hub_id | BIGINT | Nullable |
| location_id | BIGINT | Nullable |
| hub_name | VARCHAR | Nullable |
| hub_latitude | FLOAT | Nullable |
| hub_longitude | FLOAT | Nullable |
| drone_latitude | FLOAT | Non-nullable |
| drone_longitude | FLOAT | Non-nullable |
| distance_to_hub_m | FLOAT | Nullable |
| passed_at | DATETIME | Non-nullable |
| checkpoint_order | INTEGER | Nullable |

### Bảng mission_legs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Nullable |
| sequence_no | INTEGER | Nullable |
| from_hub_id | BIGINT | Nullable |
| to_hub_id | BIGINT | Nullable |
| battery_id | VARCHAR(50) | **FK**, Nullable |
| status | VARCHAR(20) | Nullable (`mission_leg_status_enum`) |
| started_at | DATETIME | Nullable |
| completed_at | DATETIME | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |

### Bảng mission_reports
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| report_url_path | VARCHAR | Non-nullable |
| generated_at | DATETIME | Non-nullable |

### Bảng missions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable (Chuẩn `MSN-XXXX`) |
| order_id | VARCHAR(50) | Nullable |
| customer_id | VARCHAR | Nullable |
| operator_id | VARCHAR | Nullable |
| drone_id | VARCHAR(50) | Nullable (Chuẩn `DRN-XXXX`) |
| battery_id | VARCHAR(50) | Nullable |
| pickup_location_id | BIGINT | Nullable |
| dropoff_location_id | BIGINT | Nullable |
| payload_weight | FLOAT | Nullable |
| distance_m | FLOAT | Nullable |
| delivery_fee | FLOAT | Nullable |
| status | VARCHAR(30) | Non-nullable (`missions_status`) |
| handling_status | VARCHAR(30) | Nullable (`missions_handling_status`) |
| scheduled_time | DATETIME | Nullable |
| start_time | DATETIME | Nullable |
| end_time | DATETIME | Nullable |
| origin_hub_id | BIGINT | Nullable |
| destination_hub_id | BIGINT | Nullable |
| departed_at | DATETIME | Nullable |
| arrived_at | DATETIME | Nullable |
| arrival_confirmed_by | VARCHAR | Nullable |
| arrival_confirmed_at | DATETIME | Nullable |

### Bảng notifications
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| user_id | VARCHAR | Non-nullable |
| title | VARCHAR | Non-nullable |
| message | VARCHAR | Non-nullable |
| notifications_type | VARCHAR(30) | Non-nullable (`notifications_type`) |
| reference_id | VARCHAR | Nullable |
| is_read | BOOLEAN | Non-nullable |
| created_at | DATETIME | Non-nullable |
| drone_id | VARCHAR(50) | Nullable |
| work_order_id | INTEGER | Nullable |
| mission_id | VARCHAR | Nullable |

### Bảng orders
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable (Chuẩn `ORD-XXXX`) |
| customer_id | VARCHAR(50) | Nullable |
| sender_phone | VARCHAR(20) | Nullable |
| receiver_name | VARCHAR(100) | Nullable |
| receiver_phone | VARCHAR(20) | Nullable |
| receiver_email | VARCHAR(255) | Nullable |
| package_label | VARCHAR(255) | Nullable |
| package_type | VARCHAR(100) | Nullable |
| payload_kg | FLOAT | Nullable |
| origin_hub_id | BIGINT | Nullable |
| destination_hub_id | BIGINT | Nullable |
| delivery_mode | VARCHAR(20) | Nullable (`delivery_mode_enum`) |
| requested_delivery_at | DATETIME | Nullable |
| estimated_window_start | DATETIME | Nullable |
| estimated_window_end | DATETIME | Nullable |
| planning_at | DATETIME | Nullable |
| status | VARCHAR(30) | Nullable (`orders_status_enum`) |
| origin_received_at | DATETIME | Nullable |
| origin_received_by | VARCHAR(50) | Nullable |
| destination_received_at | DATETIME | Nullable |
| destination_received_by | VARCHAR(50) | Nullable |
| replan_required_at | DATETIME | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |
| cancelled_at | DATETIME | Nullable |
| delivery_fee | FLOAT | Nullable |
| payment_status | VARCHAR(20) | Nullable (`payment_status_enum`) |
| payment_order_code | BIGINT | Unique, Nullable |
| payment_transaction_id | VARCHAR(100) | Nullable |
| payment_checkout_url | VARCHAR(500) | Nullable |
| payment_qr_code | VARCHAR(1000) | Nullable |
| payment_bin | VARCHAR(20) | Nullable |
| payment_account_number | VARCHAR(50) | Nullable |
| payment_account_name | VARCHAR(100) | Nullable |
| paid_at | DATETIME | Nullable |
| is_aging_priority | BOOLEAN | Nullable (Có script migration `migrate_aging_priority_schema.py`) |
| aging_deadline | DATETIME | Nullable (Có script migration `migrate_aging_priority_schema.py`) |

### Bảng password_reset_tokens
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| user_id | VARCHAR | Non-nullable |
| token | VARCHAR | Unique, Non-nullable |
| expires_at | DATETIME | Non-nullable |
| used | BOOLEAN | Non-nullable |
| created_at | DATETIME | Non-nullable |

### Bảng payments
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable |
| mission_id | VARCHAR(50) | Non-nullable |
| amount | FLOAT | Non-nullable |
| payment_method | VARCHAR(20) | Non-nullable |
| transaction_id | VARCHAR | Nullable |
| status | VARCHAR(20) | Non-nullable |
| created_at | DATETIME | Nullable |
| paid_at | DATETIME | Nullable |

### Bảng roles
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable |
| role_name | VARCHAR | Unique, Non-nullable |
| description | VARCHAR | Nullable |

### Bảng telemetry_logs
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

### Bảng users
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable |
| role_id | VARCHAR | Nullable |
| password_hash | VARCHAR | Nullable |
| full_name | VARCHAR | Non-nullable |
| email | VARCHAR | Unique, Non-nullable |
| phone | VARCHAR(20) | Nullable |
| is_active | BOOLEAN | Non-nullable |
| created_at | DATETIME | Non-nullable |
| hub_id | INTEGER | Nullable |

### Bảng work_order_logs
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| work_order_id | INTEGER | Nullable |
| action | VARCHAR | Nullable |
| from_status | VARCHAR | Nullable |
| to_status | VARCHAR | Nullable |
| changed_by | VARCHAR | Nullable |
| created_at | DATETIME | Nullable |

### Bảng work_orders
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| drone_id | VARCHAR(50) | Nullable (Chuẩn `DRN-XXXX`) |
| battery_id | VARCHAR(50) | Nullable |
| technician_id | VARCHAR | Non-nullable |
| issue_description | TEXT | Non-nullable |
| action_taken | TEXT | Nullable |
| status | VARCHAR(20) | Non-nullable (`work_orders_status`) |
| resolved_at | DATETIME | Nullable |
| alert_id | BIGINT | Nullable |
| title | VARCHAR | Nullable |
| priority | VARCHAR | Nullable |
| created_by | VARCHAR | Nullable |
| assigned_to | VARCHAR | Nullable |
| scheduled_at | DATETIME | Nullable |
| started_at | DATETIME | Nullable |
| completed_at | DATETIME | Nullable |
