# Database Schema Documentation
Đây là tài liệu Single Source of Truth cho cấu trúc cơ sở dữ liệu của dự án Drone-backend, được tự động cập nhật và đồng bộ theo models.

## I. Cấu trúc các Bảng (Tables)

### Bảng ai_predictions
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | BIGINT | Non-nullable |
| estimated_energy_wh | FLOAT | Non-nullable |
| estimated_duration_m | FLOAT | Non-nullable |
| risk_level | VARCHAR(6) | Non-nullable |
| shap_values_json | JSONB | Nullable |
| predicted_at | DATETIME | Non-nullable |

### Bảng ai_recommendations
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| mission_id | BIGINT | Non-nullable |
| category | VARCHAR(20) | Non-nullable |
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
| drone_id | BIGINT | **FK**, Nullable |
| serial_number | VARCHAR(100) | Unique, Non-nullable |
| capacity_wh | FLOAT | Non-nullable |
| status | VARCHAR(8) | Non-nullable |
| current_hub_id | INTEGER | **FK**, Nullable |
| charge_level_pct | BIGINT | Nullable |
| create_at | DATETIME | Nullable |
| update_at | DATETIME | Nullable |
| battery_model | VARCHAR(100) | Nullable |

### Bảng drones
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| name | VARCHAR(100) | Non-nullable |
| model | VARCHAR(100) | Non-nullable |
| payload_capacity_kg | FLOAT | Non-nullable |
| max_speed | FLOAT | Non-nullable |
| operational_status | VARCHAR(11) | Non-nullable |
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
| drone_id | BIGINT | Nullable |
| reporter_id | VARCHAR | Nullable |
| severity | VARCHAR(6) | Non-nullable |
| description | VARCHAR | Non-nullable |
| status | VARCHAR(13) | Non-nullable |
| reported_at | DATETIME | Nullable |
| requires_technical_inspection | BOOLEAN | Non-nullable |

### Bảng locations
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | BIGINT | **PK**, Non-nullable |
| name | VARCHAR | Non-nullable |
| latitude | FLOAT | Non-nullable |
| longitude | FLOAT | Non-nullable |
| type | VARCHAR(15) | Non-nullable |

### Bảng maintenance_alerts
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | INTEGER | **PK**, Non-nullable |
| drone_id | INTEGER | Nullable |
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
| drone_id | INTEGER | Nullable |
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
| drone_id | INTEGER | Nullable |
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
| status | VARCHAR(11) | Nullable |
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
| id | VARCHAR(50) | **PK**, Non-nullable |
| order_id | VARCHAR(50) | Nullable |
| customer_id | VARCHAR | Nullable |
| operator_id | VARCHAR | Nullable |
| drone_id | BIGINT | Nullable |
| battery_id | VARCHAR(50) | Nullable |
| pickup_location_id | BIGINT | Nullable |
| dropoff_location_id | BIGINT | Nullable |
| payload_weight | FLOAT | Nullable |
| distance_m | FLOAT | Nullable |
| delivery_fee | FLOAT | Nullable |
| status | VARCHAR(17) | Non-nullable |
| handling_status | VARCHAR(15) | Nullable |
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
| notifications_type | VARCHAR(14) | Non-nullable |
| reference_id | VARCHAR | Nullable |
| is_read | BOOLEAN | Non-nullable |
| created_at | DATETIME | Non-nullable |
| drone_id | INTEGER | Nullable |
| work_order_id | INTEGER | Nullable |
| mission_id | VARCHAR | Nullable |

### Bảng orders
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc |
| :--- | :--- | :--- |
| id | VARCHAR(50) | **PK**, Non-nullable |
| customer_id | VARCHAR(50) | Nullable |
| sender_phone | VARCHAR(20) | Nullable |
| receiver_phone | VARCHAR(20) | Nullable |
| package_label | VARCHAR(255) | Nullable |
| package_type | VARCHAR(100) | Nullable |
| payload_kg | FLOAT | Nullable |
| origin_hub_id | BIGINT | Nullable |
| destination_hub_id | BIGINT | Nullable |
| delivery_mode | VARCHAR(9) | Nullable |
| requested_delivery_at | DATETIME | Nullable |
| estimated_window_start | DATETIME | Nullable |
| estimated_window_end | DATETIME | Nullable |
| planning_at | DATETIME | Nullable |
| status | VARCHAR(16) | Nullable |
| origin_received_at | DATETIME | Nullable |
| origin_received_by | VARCHAR(50) | Nullable |
| destination_received_at | DATETIME | Nullable |
| destination_received_by | VARCHAR(50) | Nullable |
| replan_required_at | DATETIME | Nullable |
| created_at | DATETIME | Nullable |
| updated_at | DATETIME | Nullable |
| cancelled_at | DATETIME | Nullable |
| delivery_fee | FLOAT | Nullable |
| payment_status | VARCHAR(9) | Nullable |
| payment_order_code | BIGINT | Unique, Nullable |
| payment_transaction_id | VARCHAR(100) | Nullable |
| payment_checkout_url | VARCHAR(500) | Nullable |
| paid_at | DATETIME | Nullable |

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
| payment_method | VARCHAR(11) | Non-nullable |
| transaction_id | VARCHAR | Nullable |
| status | VARCHAR(9) | Non-nullable |
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
| drone_id | BIGINT | Non-nullable |
| battery_id | VARCHAR(50) | Nullable |
| technician_id | VARCHAR | Non-nullable |
| issue_description | TEXT | Non-nullable |
| action_taken | TEXT | Nullable |
| status | VARCHAR(11) | Non-nullable |
| resolved_at | DATETIME | Nullable |
| alert_id | BIGINT | Nullable |
| title | VARCHAR | Nullable |
| priority | VARCHAR | Nullable |
| created_by | VARCHAR | Nullable |
| assigned_to | VARCHAR | Nullable |
| scheduled_at | DATETIME | Nullable |
| started_at | DATETIME | Nullable |
| completed_at | DATETIME | Nullable |
