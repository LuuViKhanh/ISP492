
# DroneOptAI – Actors, Use Cases và Main Flows

## Thông tin đề tài

- **Tên đề tài:** Intelligent Drone Energy Analytics and Operational Management System
- **Tên viết tắt:** DroneOptAI
- **Capstone:** Information System
- **Thời gian:** 05/2026 – 12/2026
- **Supervisor:** Assoc. Prof. Đặng Ngọc Minh Đức

---

# 1. Actors

| Actor | Vai trò | Giao diện |
|---|---|---|
| Customer | Tạo và theo dõi yêu cầu mission, xem prediction và báo cáo | Mobile App |
| Operator | Quản lý mission, lập kế hoạch, phê duyệt, theo dõi chuyến bay, sử dụng AI analytics | Web App |
| Technician | Quản lý drone, battery, bảo trì, inspection, battery replacement | Web App |
| Admin | Quản trị tài khoản, RBAC, cấu hình hệ thống, audit log, backup | Web App |
| Open-Meteo API *(External System)* | Cung cấp dữ liệu thời tiết phục vụ AI prediction | External API |

---

# 2. Use Case Overview

## Customer

- Create Mission Request
- Track Mission Status
- View Prediction Result
- Receive Notification
- Download Mission Report

## Operator

- Review Mission Request
- Plan Mission
- Submit Mission for Approval
- Approve / Reject Mission
- Assign Drone & Battery
- Execute Flight
- Monitor Flight
- Upload Telemetry
- View AI Prediction
- View AI Recommendation
- Perform What-if Analysis
- Review Mission Result

## Technician

- Manage Drone
- Manage Battery
- Monitor Battery Health
- Create Maintenance Work Order
- Perform Inspection
- Update Maintenance Record
- Request Battery Replacement
- View Maintenance Alert

## Admin

- Manage User Accounts
- Manage Roles & Permissions
- Configure System
- Manage Notification Settings
- Manage Backup & Data Retention
- View Audit Logs
- View Operation Logs

---

# 3. Use Case Packages

## Package 1 – Mission Management

### Actors
Customer, Operator

### Use Cases

| Use Case | Actor |
|---|---|
| Create Mission Request | Customer |
| Track Mission | Customer |
| View Mission Status | Customer |
| View Prediction Result | Customer |
| Review Mission Request | Operator |
| Plan Mission | Operator |
| Submit Mission for Approval | Operator |

**Include Relationships**

- Plan Mission
  - Define Route
  - Define Payload
  - Define Altitude
  - Define Schedule
  - Perform Pre-flight Analysis

---

## Package 2 – Mission Approval & Flight Operation

### Actor
Operator

### Use Cases

| Use Case | Description |
|---|---|
| Review Planned Mission | Kiểm tra thông tin mission |
| Check Drone Availability | Kiểm tra drone khả dụng |
| Check Battery Health | Kiểm tra SOH và battery cycle |
| Perform Risk Pre-check | Đánh giá rủi ro bằng AI |
| Approve Mission | Phê duyệt mission |
| Reject Mission | Từ chối mission |
| Assign Drone | Gán drone |
| Assign Operator | Gán operator |
| Execute Flight | Thực hiện chuyến bay |
| Monitor Flight | Theo dõi telemetry thời gian thực |
| Submit Flight Report | Gửi báo cáo chuyến bay |

**Include Relationships**

- Approve Mission
  - Check Drone Availability
  - Check Battery Health
  - Perform Risk Pre-check

---

## Package 3 – Energy Analytics & AI Recommendation

### Actor
Operator

### Use Cases

| Use Case | AI Service |
|---|---|
| Analyze Telemetry Data | Telemetry Analytics |
| Analyze Weather Data | Weather Integration |
| Predict Energy Consumption | ML Regression |
| Predict Flight Duration | ML Regression |
| Calculate Energy Efficiency | Analytics KPI |
| Explain Prediction | SHAP Explainable AI |
| Assess Mission Risk | AI Risk Engine |
| Rank Routes | Route Analytics |
| Recommend Route | Recommendation Engine |
| Perform What-if Analysis | Decision Support |

**Include Relationships**

- Predict Energy Consumption
  - Explain Prediction
  - Assess Mission Risk

- Recommend Route
  - Calculate Energy Efficiency
  - Rank Routes

- What-if Analysis
  - Predict Energy Consumption
  - Assess Mission Risk
  - Explain Prediction

---

## Package 4 – Fleet, Battery & Maintenance

### Actor
Technician

### Use Cases

| Use Case | Description |
|---|---|
| Manage Drone | CRUD drone |
| Manage Battery | CRUD battery |
| Monitor Battery Health | SOH, cycles |
| Monitor Drone Status | Flight hours, availability |
| View Maintenance Alert | Nhận cảnh báo |
| Create Maintenance Work Order | Sinh work order |
| Perform Inspection | Kiểm tra drone |
| Record Maintenance | Lưu lịch sử bảo trì |
| Record Spare Parts | Linh kiện sử dụng |
| Request Battery Replacement | Thay pin |

**Relationships**

- Monitor Battery Health
  - *extend* Request Battery Replacement

- View Maintenance Alert
  - *include* Create Maintenance Work Order

---

## Package 5 – Post-flight Review, Incident & Reporting

### Actors
Operator, Technician, Admin

### Use Cases

| Use Case | Actor |
|---|---|
| Review Completed Mission | Operator |
| Compare Actual vs Predicted | Operator |
| Analyze Deviation | Operator |
| Report Incident | Operator |
| Investigate Incident | Technician |
| Record Corrective Action | Technician |
| View KPI Dashboard | Admin |
| Generate Mission Report | Admin |
| Export KPI Report | Admin |

**Relationships**

- Review Completed Mission
  - Compare Actual vs Predicted
  - Analyze Deviation

- Report Incident
  - Investigate Incident
  - Record Corrective Action

---

# 4. 5 Main Flows (chia cho 5 sinh viên)

## MF1 – Mission Request & Planning (SV1)

### Mục tiêu

Quản lý yêu cầu giao hàng và lập kế hoạch mission.

### Flow

```text
Customer
   ↓
Create Mission Request
   ↓
Enter Delivery Requirements
   ↓
Operator Review
   ↓
Plan Mission
(Route, Payload, Altitude, Schedule)
   ↓
Select Drone & Battery
   ↓
Pre-flight Analysis
   ↓
Submit Mission for Approval
```

### Thành phần

- Customer
- Operator
- Mission Module
- Pre-flight Analysis

### Deliverables

- Mission CRUD
- Planning UI
- Validation
- Notification gửi approval request

---

## MF2 – Mission Approval & Flight Operation (SV2)

### Mục tiêu

Thực hiện quy trình phê duyệt và vận hành chuyến bay.

### Flow

```text
Planned Mission
      ↓
Review Mission
      ↓
Check Drone Availability
      ↓
Check Battery Health
      ↓
Risk Prediction
      ↓
Approve / Reject
      ↓
Assign Drone & Operator
      ↓
Execute Flight
      ↓
Collect Telemetry
```

### Thành phần

- Approval Workflow
- Telemetry Streaming
- Mission Tracking
- Notification

### Deliverables

- Approval Engine
- Flight Monitoring
- Telemetry Upload API

---

## MF3 – Energy Analytics & AI Recommendation (SV3)

### Mục tiêu

Phân tích dữ liệu và sinh recommendation.

### Flow

```text
Telemetry Data
      +
Weather Data
      ↓
Data Validation
      ↓
Feature Engineering
      ↓
ML Prediction
      ↓
Energy Consumption
Flight Duration
Energy Efficiency
      ↓
SHAP Explanation
      ↓
Risk Assessment
      ↓
Route Ranking
      ↓
Recommendation
```

### Thành phần

- Data Pipeline
- ML Models
- SHAP
- Recommendation Engine

### Deliverables

- Prediction Service API
- SHAP Dashboard
- Route Recommendation

---

## MF4 – Fleet, Battery & Maintenance (SV4)

### Mục tiêu

Quản lý vòng đời drone và battery.

### Flow

```text
Technician
     ↓
Manage Drone
     ↓
Manage Battery
     ↓
Monitor SOH
     ↓
Detect Battery Degradation
     ↓
Maintenance Alert
     ↓
Work Order
     ↓
Inspection
     ↓
Update Status
```

### Thành phần

- Fleet Module
- Battery Lifecycle
- Maintenance Module

### Deliverables

- Drone Management
- Battery Management
- Maintenance Records

---

## MF5 – Post-flight Review, Incident & Reporting (SV5)

### Mục tiêu

Đánh giá chuyến bay sau khi hoàn thành và tạo KPI.

### Flow

```text
Flight Completed
      ↓
Post-flight Analysis
      ↓
Compare Actual vs Predicted
      ↓
Deviation Analysis
      ↓
Incident Detection
      ↓
Investigation
      ↓
Corrective Action
      ↓
Mission KPI Dashboard
      ↓
Generate Report
      ↓
Archive Mission
```

### Thành phần

- Mission Review
- Incident Management
- KPI Dashboard
- Reporting

### Deliverables

- KPI Analytics
- Mission Reports
- Incident History

---

# 5. Mapping Main Flows – Actors – Modules

| Main Flow | Actor chính | Module |
|---|---|---|
| MF1 – Mission Request & Planning | Customer, Operator | Mission Management |
| MF2 – Mission Approval & Flight Operation | Operator | Workflow Engine, Telemetry |
| MF3 – Energy Analytics & AI Recommendation | Operator | AI Prediction, SHAP, Recommendation |
| MF4 – Fleet, Battery & Maintenance | Technician | Fleet & Maintenance |
| MF5 – Post-flight Review & Reporting | Operator, Technician, Admin | KPI, Incident, Reporting |

---

# 6. Tổng thể hệ thống (Level-0 Business Flow)

```text
Customer
   │
   ▼
Mission Request & Planning
        │
        ▼
Mission Approval & Flight Operation
        │
        ▼
Telemetry + Weather Data
        │
        ▼
Energy Analytics & AI Recommendation
        │
        ▼
Fleet / Battery / Maintenance
        │
        ▼
Post-flight Review & Reporting
        │
        ▼
KPI Dashboard & Feedback
        │
        └──────────────► AI Model Retraining
```

---

# 7. Phân công 5 sinh viên

| Sinh viên | Phần phụ trách | Trọng tâm |
|---|---|---|
| **SV1** | Mission Request & Planning | Business Process, Mission CRUD, Planning |
| **SV2** | Mission Approval & Flight Operation | Workflow, Approval, Telemetry |
| **SV3** | Energy Analytics & AI Recommendation | Machine Learning, SHAP, Recommendation |
| **SV4** | Fleet, Battery & Maintenance | Drone, Battery Lifecycle, Maintenance |
| **SV5** | Post-flight Review, Incident & Reporting | KPI Dashboard, Incident, Reporting, Feedback |

---

## Ghi chú thiết kế UML

### Actors chính

- Customer
- Operator
- Technician
- Admin

### External System

- Open-Meteo API

### Tổng số Use Cases đề xuất

- Customer: **5**
- Operator: **10**
- Technician: **8**
- Admin: **7**

**Tổng cộng khoảng 30 Use Cases**, phù hợp để xây dựng:
- 01 Use Case Diagram tổng thể.
- 05 Use Case Diagrams chi tiết (mỗi Main Flow một diagram).
