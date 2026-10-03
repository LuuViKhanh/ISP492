# Database Schema Documentation
Đây là tài liệu Single Source of Truth cho cấu trúc cơ sở dữ liệu của dự án Drone-backend, được tự động cập nhật và đồng bộ theo models.

## I. Danh sách Enums (Kiểu dữ liệu liệt kê)

| Tên Enum | Các giá trị (Values) |
| :--- | :--- |
| drone_status | Available, In Flight, Maintenance, Retired |
| drone_status_enum | AVAILABLE, IN_MISSION, MAINTENANCE, RETIRED |
| atteries_status | Active, Replaced |
| attery_status | Fully Charged, In Use, Charging, Degraded |
| attery_status_enum | FULLY_CHARGED, IN_USE, CHARGING, DEGRADED |
| locations_type | Hub, CustomerAddress, MiniHub |
| missions_status | Awaiting Payment, Pending Approval, Approved, Rejected, Flying, Completed, Cancelled, Active mission, Mission completed, Incident return |
| missions_handling_status | Incoming, At hub, Ready, Cannot continue |
| mission_leg_status_enum | PENDING, IN_PROGRESS, COMPLETED |
| payment_method | VNPay, Momo, Credit Card, Cash |
| payments_status | Pending, Success, Failed, Refunded |
| payment_status_enum | UNPAID, PENDING, PAID, FAILED, CANCELLED, REFUNDED |
| 
isk_level | Low, Medium, High |
| category | Mission Planning, Maintenance, Battery Health, Payload Optimisation, Weather Alert, Risk Assessment, Operator Assignment |
| incidents_status | Open, Investigating, Closed |
| work_orders_status | Pending, In Progress, Completed |
| udit_logs_action | CREATE, UPDATE, APPROVE, LOGIN, LOGOUT, REGISTER, PASSWORD_RESET, UPDATE_PROFILE, DELETE |
| userrole | ADMIN, OPERATOR, TECHNICIAN, CUSTOMER |
| 
otifications_type | Mission_Status, Payment, System_Alert, AI_Warning, Maintenance |
| delivery_mode_enum | EXPRESS, SCHEDULED |
| orders_status_enum | PENDING, IN_DELIVERY, DELIVERED_TO_HUB, CANCELLED |

---

## II. Cấu trúc các Bảng (Tables)

### 1. Nhóm Auth & Người dùng
**Bảng 
oles**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable | |
| 
ole_name | VARCHAR | Unique, Non-nullable | |
| description | VARCHAR | Nullable | |

**Bảng users**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable | |
| 
ole_id | VARCHAR | **FK**, Non-nullable | |
| password_hash | VARCHAR | Non-nullable | |
| ull_name | VARCHAR | Non-nullable | |
| email | VARCHAR | Unique, Non-nullable | |
| is_active | BOOL | Non-nullable | |
| created_at | TIMESTAMPTZ | Non-nullable | |
| hub_id | INT4 | Nullable | |

**Bảng password_reset_tokens**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Non-nullable | |
| user_id | VARCHAR | Non-nullable | |
| 	oken | TEXT | Unique, Non-nullable | |
| expires_at | TIMESTAMPTZ | Non-nullable | |
| used | BOOL | Non-nullable | |
| created_at | TIMESTAMPTZ | Non-nullable | |


### 2. Nhóm Địa điểm & Trạm
**Bảng hubs**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| code | VARCHAR | Unique, Nullable | |
| 
ame | VARCHAR | Nullable | |
| ddress | VARCHAR | Nullable | |
| latitude | NUMERIC | Nullable | |
| longitude | NUMERIC | Nullable | |
| status | VARCHAR | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| updated_at | TIMESTAMP | Nullable | |

**Bảng locations**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| 
ame | VARCHAR | Non-nullable | |
| latitude | FLOAT8 | Non-nullable | Vĩ độ |
| longitude | FLOAT8 | Non-nullable | Kinh độ |
| 	ype | LOCATIONS_TYPE | Non-nullable | |


### 3. Nhóm Quản lý Đội bay (Fleet)
**Bảng drones**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| 
ame | VARCHAR | Non-nullable | |
| model | VARCHAR | Non-nullable | |
| payload_capacity_kg | FLOAT8 | Non-nullable | Sức chở tối đa (kg) |
| max_speed | FLOAT8 | Non-nullable | |
| operational_status | DRONE_STATUS | Non-nullable | |
| created_at | TIMESTAMPTZ | Non-nullable | |
| current_hub_id | INT4 | Nullable | |
| attery_level_pct | INT4 | Nullable | |
| utilization_pct | NUMERIC | Nullable | |

**Bảng atteries**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| drone_id | INT8 | **FK**, Nullable | Lắp trên Drone nào (Null = trong kho) |
| serial_number | VARCHAR | Unique, Non-nullable | |
| capacity_wh | FLOAT8 | Non-nullable | Dung lượng (Watt-hour) |
| status | BATTERIES_STATUS | Non-nullable | |
| current_hub_id | INT4 | **FK**, Nullable | Pin đang nằm ở hub nào |
| charge_level_pct | INT8 | Nullable | % pin đang có |
| create_at | TIMESTAMPTZ | Nullable | |
| update_at | TIMESTAMPTZ | Nullable | |
| attery_model | VARCHAR | Nullable | |


### 4. Nhóm Đơn hàng, Chuyến bay & Thanh toán
**Bảng orders**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable | |
| customer_id | VARCHAR | Nullable | |
| package_label | VARCHAR | Nullable | |
| package_type | VARCHAR | Nullable | |
| payload_kg | NUMERIC | Nullable | |
| origin_hub_id | INT4 | Nullable | |
| destination_hub_id | INT4 | Nullable | |
| delivery_mode | VARCHAR | Nullable | |
| 
equested_delivery_at | TIMESTAMP | Nullable | |
| estimated_window_start | TIMESTAMP | Nullable | |
| estimated_window_end | TIMESTAMP | Nullable | |
| planning_at | TIMESTAMP | Nullable | |
| status | VARCHAR | Nullable | |
| origin_received_at | TIMESTAMP | Nullable | |
| origin_received_by | VARCHAR | Nullable | |
| destination_received_at| TIMESTAMP | Nullable | |
| destination_received_by| VARCHAR | Nullable | |
| 
eplan_required_at | TIMESTAMP | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| updated_at | TIMESTAMP | Nullable | |
| cancelled_at | TIMESTAMP | Nullable | |

**Bảng missions**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| customer_id | VARCHAR | Non-nullable | |
| operator_id | VARCHAR | Nullable | |
| drone_id | INT8 | **FK**, Nullable | |
| attery_id | INT8 | **FK**, Nullable | |
| pickup_location_id | INT4 | **FK**, Nullable | |
| dropoff_location_id | INT4 | **FK**, Nullable | |
| payload_weight | FLOAT8 | Non-nullable | |
| distance_m | FLOAT8 | Non-nullable | Khoảng cách tuyến (mét) |
| delivery_fee | FLOAT8 | Nullable | Phí giao hàng tính toán |
| status | MISSIONS_STATUS | Non-nullable | |
| scheduled_time | TIMESTAMP | Non-nullable | Dự kiến bay |
| start_time | TIMESTAMP | Nullable | Cất cánh thực tế |
| end_time | TIMESTAMP | Nullable | Hạ cánh thực tế |
| origin_hub_id | INT4 | Nullable | |
| destination_hub_id | INT4 | Nullable | |
| departed_at | TIMESTAMP | Nullable | |
| rrived_at | TIMESTAMP | Nullable | |
| rrival_confirmed_by | VARCHAR | Nullable | |
| rrival_confirmed_at | TIMESTAMP | Nullable | |
| pproval_deadline | TIMESTAMP | Nullable | Countdown Timeout |
| handling_status | MISSIONS_HANDLING_STATUS| Nullable | |
| order_code | VARCHAR | Nullable | |
| mission_code | VARCHAR | Nullable | |
| order_id | VARCHAR | Nullable | |
| 
oute_id | VARCHAR | Nullable | |
| planned_start_at | TIMESTAMP | Nullable | |
| package_receive_deadline_at| TIMESTAMP | Nullable | |
| estimated_arrival_at | TIMESTAMP | Nullable | |
| ctual_started_at | TIMESTAMP | Nullable | |
| ctual_completed_at | TIMESTAMP | Nullable | |
| predicted_duration_min| INT4 | Nullable | |
| estimated_energy_wh | NUMERIC | Nullable | |
| attery_consumption_pct| INT4 | Nullable | |
| predicted_remaining_battery_pct| INT4 | Nullable | |
| confidence_pct | INT4 | Nullable | |
| 
isk_level | VARCHAR | Nullable | |
| created_by | VARCHAR | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| updated_at | TIMESTAMP | Nullable | |
| cancel_reason | TEXT | Nullable | |
| ailure_reason | TEXT | Nullable | |

**Bảng mission_legs**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| mission_id | INT4 | Nullable | |
| sequence_no | INT4 | Nullable | |
| rom_hub_id | INT4 | Nullable | |
| 	o_hub_id | INT4 | Nullable | |
| status | VARCHAR | Nullable | |
| started_at | TIMESTAMP | Nullable | |
| completed_at | TIMESTAMP | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| updated_at | TIMESTAMP | Nullable | |
| attery_id | INT8 | **FK**, Nullable | |

**Bảng mission_battery_swap_history**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| mission_id | INT8 | Nullable | |
| hub_id | INT4 | Nullable | |
| old_battery_id | INT8 | Nullable | |
| 
ew_battery_id | INT8 | Nullable | |
| swapped_at | TIMESTAMPTZ | Nullable | |

**Bảng mission_hub_checkpoints**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Non-nullable | |
| mission_id | INT8 | Non-nullable | |
| hub_id | INT8 | Nullable | |
| location_id | INT8 | Nullable | |
| hub_name | VARCHAR | Nullable | |
| hub_latitude | FLOAT8 | Nullable | |
| hub_longitude| FLOAT8 | Nullable | |
| drone_latitude| FLOAT8 | Non-nullable | |
| drone_longitude| FLOAT8 | Non-nullable | |
| distance_to_hub_m| FLOAT8 | Nullable | |
| passed_at | TIMESTAMP | Non-nullable | |
| checkpoint_order| INT4 | Nullable | |

**Bảng mission_reports**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| mission_id | INT8 | **FK**, Non-nullable | |
| 
eport_url_path| VARCHAR | Non-nullable | File PDF/Word |
| generated_at | TIMESTAMPTZ | Non-nullable | Thời gian xuất file |

**Bảng payments**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | VARCHAR | **PK**, Non-nullable | |
| mission_id | INT8 | **FK**, Non-nullable | |
| mount | FLOAT8 | Non-nullable | Bằng delivery_fee |
| payment_method| PAYMENT_METHOD | Non-nullable | |
| 	ransaction_id| VARCHAR | Nullable | Mã thẻ, VNPay |
| status | PAYMENTS_STATUS | Non-nullable | |
| created_at | TIMESTAMPTZ | Nullable | |
| paid_at | TIMESTAMP | Nullable | |


### 5. Nhóm Bảo trì & Sự cố
**Bảng incidents**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| mission_id | INT8 | **FK**, Non-nullable | |
| drone_id | INT8 | **FK**, Non-nullable | |
| 
eporter_id | VARCHAR | Non-nullable | |
| severity | RISK_LEVEL | Non-nullable | |
| description | TEXT | Non-nullable | |
| status | INCIDENTS_STATUS | Non-nullable | |
| 
eported_at | TIMESTAMPTZ | Non-nullable | |
| 
equires_technical_inspection| BOOL | Nullable | |

**Bảng maintenance_alerts**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| drone_id | INT4 | Nullable | |
| source | VARCHAR | Nullable | |
| maintenance_schedule_id| INT4 | Nullable | |
| mission_id | VARCHAR | Nullable | |
| incident_id | INT4 | Nullable | |
| 	itle | VARCHAR | Nullable | |
| status | VARCHAR | Nullable | |
| work_order_id | INT4 | Nullable | |
| handled_by | VARCHAR | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| handled_at | TIMESTAMP | Nullable | |

**Bảng maintenance_schedules**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| drone_id | INT4 | Nullable | |
| maintenance_type| VARCHAR | Nullable | |
| interval_days | INT4 | Nullable | |
| interval_flight_hours| NUMERIC | Nullable | |
| last_inspection_at| TIMESTAMP | Nullable | |
| 
ext_inspection_at| TIMESTAMP | Nullable | |
| status | VARCHAR | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| updated_at | TIMESTAMP | Nullable | |

**Bảng work_orders**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| drone_id | INT8 | **FK**, Non-nullable | |
| attery_id | INT8 | **FK**, Non-nullable | |
| 	echnician_id | INT8 | Non-nullable | |
| issue_description| TEXT | Non-nullable | |
| ction_taken | TEXT | Nullable | |
| status | WORK_ORDERS_STATUS | Non-nullable | |
| 
esolved_at | TIMESTAMPTZ | Nullable | |
| lert_id | INT4 | Nullable | |
| 	itle | VARCHAR | Nullable | |
| priority | VARCHAR | Nullable | |
| created_by | VARCHAR | Nullable | |
| ssigned_to | VARCHAR | Nullable | |
| scheduled_at | TIMESTAMP | Nullable | |
| started_at | TIMESTAMP | Nullable | |
| completed_at | TIMESTAMP | Nullable | |

**Bảng work_order_logs**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| work_order_id | INT4 | Nullable | |
| ction | VARCHAR | Nullable | |
| rom_status | VARCHAR | Nullable | |
| 	o_status | VARCHAR | Nullable | |
| changed_by | VARCHAR | Nullable | |
| created_at | TIMESTAMP | Nullable | |

**Bảng maintenance_records**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| work_order_id | INT4 | Unique, Nullable | |
| drone_id | INT4 | Nullable | |
| performed_by | VARCHAR | Nullable | |
| 	itle | VARCHAR | Nullable | |
| diagnosis | TEXT | Nullable | |
| corrective_action| TEXT | Nullable | |
| 
esulting_drone_status| VARCHAR | Nullable | |
| created_at | TIMESTAMP | Nullable | |
| completed_at | TIMESTAMP | Nullable | |

**Bảng maintenance_inspection_items**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT4 | **PK**, Non-nullable | |
| maintenance_record_id| INT4 | Nullable | |
| item_name | VARCHAR | Nullable | |
| is_completed | BOOL | Nullable | |
| checked_at | TIMESTAMP | Nullable | |


### 6. Nhóm AI, Telemetry & Logs
**Bảng i_predictions**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| mission_id | INT8 | **FK**, Non-nullable | |
| estimated_energy_wh| FLOAT8 | Non-nullable | |
| estimated_duration_m| FLOAT8 | Non-nullable | Dự đoán bay (phút) |
| 
isk_level | RISK_LEVEL | Non-nullable | |
| shap_values_json| JSONB | Nullable | Lưu SHAP values |
| predicted_at | TIMESTAMPTZ | Non-nullable | |

**Bảng i_recommendations**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| mission_id | INT8 | **FK**, Non-nullable | |
| category | CATEGORY | Non-nullable | |
| content | TEXT | Non-nullable | |
| is_applied | BOOL | Non-nullable | |
| created_at | TIMESTAMPTZ | Non-nullable | |

**Bảng 	elemetry_logs**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| mission_id | INT8 | **FK**, Non-nullable | |
| 	imestamp | TIMESTAMP | Non-nullable | |
| latitude | FLOAT8 | Non-nullable | Vĩ độ hiện tại |
| longitude | FLOAT8 | Non-nullable | Kinh độ hiện tại |
| ltitude | FLOAT8 | Non-nullable | Độ cao (m) |
| speed | FLOAT8 | Non-nullable | Tốc độ (m/s) |
| attery_voltage| FLOAT8 | Nullable | Điện áp pin |
| energy_consumed_wh| FLOAT8 | Nullable | Năng lượng tiêu thụ |
| wind_speed | FLOAT8 | Nullable | |
| weather_temperature| FLOAT8 | Nullable | |
| weather_humidity| FLOAT8 | Nullable | |
| weather_wind_speed| FLOAT8 | Nullable | |
| weather_precipitation| FLOAT8 | Nullable | |
| weather_apparent_temp| FLOAT8 | Nullable | |
| weather_dew_point| FLOAT8 | Nullable | |
| weather_wind_gust| FLOAT8 | Nullable | |
| weather_wind_direction| FLOAT8 | Nullable | |
| weather_pressure| FLOAT8 | Nullable | |
| weather_cloud_cover| FLOAT8 | Nullable | |

**Bảng udit_logs**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| user_id | VARCHAR | Non-nullable | Null: hệ thống tự làm |
| ction | AUDIT_LOGS_ACTION| Non-nullable | |
| 
esource_table | VARCHAR | Non-nullable | Bảng bị tác động |
| details_json | JSONB | Nullable | Dữ liệu cũ/mới |
| created_at | TIMESTAMPTZ | Non-nullable | |

**Bảng 
otifications**
| Cột | Kiểu dữ liệu | Khóa / Ràng buộc | Ghi chú |
| :--- | :--- | :--- | :--- |
| id | INT8 | **PK**, Identity, Non-nullable | |
| user_id | VARCHAR | **FK**, Non-nullable | |
| 	itle | VARCHAR | Non-nullable | |
| message | VARCHAR | Non-nullable | |
| 
otifications_type| NOTIFICATIONS_TYPE| Non-nullable | |
| 
eference_id | VARCHAR | Nullable | |
| is_read | BOOL | Non-nullable | |
| created_at | TIMESTAMPTZ | Non-nullable | |
| drone_id | INT4 | Nullable | |
| work_order_id| INT4 | Nullable | |
| mission_id | VARCHAR | Nullable | |
