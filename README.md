# DroneOptAI 🚁

**Energy-Efficient Drone Delivery System** — FA26IS01

> Hệ thống thông tin phân tích hiệu suất năng lượng và gợi ý tuyến đường tối ưu cho mạng lưới giao hàng bằng UAV đa điểm xuất phát.

---

## 📁 Cấu trúc dự án

```text
project/
├── app/
│   ├── main.py                      # API Gateway: nơi kết nối tất cả các module
│   ├── core/
│   │   └── config.py                # Cấu hình hệ thống, biến môi trường (.env)
│   ├── shared/
│   │   ├── roles.py                 # Enum định nghĩa các Role (Admin, Operator, Technician, Customer)
│   │   └── dependencies.py          # Hệ thống phân quyền RBAC dùng chung
│   └── modules/                     # Các module nghiệp vụ độc lập
│       ├── auth/                    # Đăng nhập, đăng xuất, JWT token management
│       ├── users/                   # Quản lý tài khoản người dùng
│       ├── missions/                # Quản lý chuyến bay, yêu cầu, duyệt lịch
│       ├── fleet/                   # Quản lý Drone, Pin, Bảo trì
│       ├── ai_predictions/          # Dự đoán năng lượng, SHAP, đề xuất tuyến đường
│       ├── payments/                # Cổng thanh toán, quản lý hóa đơn & hoàn tiền
│       └── system/                  # Audit logs, cấu hình hệ thống, backup
├── requirements.txt
├── .env                             # Biến môi trường (KHÔNG commit lên Git)
└── README.md

<!-- 
frontend/
│ ├── public/
│ │ ├── favicon.ico
│ │ └── ...
│ │
│ ├── src/
│ │ ├── assets/
│ │ │ ├── images/
│ │ │ ├── icons/
│ │ │ ├── styles/
│ │ │ └── ...
│ │ │
│ │ ├── components/
│ │ │ ├── common/
│ │ │ ├── ui/
│ │ │ └── ...
│ │ │
│ │ ├── pages/
│ │ │ ├── Home/
│ │ │ ├── Login/
│ │ │ └── ...
│ │ │
│ │ ├── layouts/
│ │ │ ├── MainLayout.jsx
│ │ │ └── ...
│ │ │
│ │ ├── services/
│ │ │ ├── api.js
│ │ │ └── ...
│ │ │
│ │ ├── hooks/
│ │ ├── utils/
│ │ ├── context/
│ │ │
│ │ ├── App.jsx
│ │ ├── main.jsx
│ │ └── index.css
│ │
│ ├── index.html
│ ├── package.json
│ ├── vite.config.js
│ └── README.md
│
├── .gitignore
├── .env.example
└── README.md

Customer
│
├── Dashboard
│   ├── Active Deliveries
│   ├── Completed Deliveries
│   ├── Pending Payment
│   └── Delivery Statistics
│
├── My Deliveries
│   ├── All Deliveries
│   ├── Create Delivery
│   │   ├── Pickup Location
│   │   ├── Delivery Location
│   │   ├── Package Information
│   │   ├── Payload
│   │   └── Preferred Time
│   │
│   └── Delivery Detail
│       ├── Delivery Information
│       ├── Route
│       ├── Duration Estimate
│       ├── Delivery Status
│       └── Mission Report (Download)
│
├── Payment
│   ├── Order Summary
│   ├── Delivery Fee
│   ├── Payment Method
│   └── Confirm Payment
│
├── Payment Result
│   ├── Success
│   └── Failed
│
├── Tracking
│   ├── Drone Location
│   ├── Route
│   ├── ETA (mốc thời gian dự kiến giao)
│   └── Delivery Status
│
├── Notifications
│
└── Profile


Operator
│
├── Dashboard
│   ├── Mission KPI
│   ├── Mission Status
│   ├── Active Flights
│   ├── Energy KPI
│   ├── Route Efficiency
│   └── Alerts
│
├── Missions
│   ├── All Missions
│   ├── Create Mission
│   ├── Mission Detail
│   └── Approval Queue
│       ├── Approve
│       └── Reject (Kích hoạt Refund)
│
├── Operations
│   ├── Live Tracking
│   ├── Telemetry
│   │   ├── Upload Flight Logs
│   │   ├── Validation
│   │   └── Flight Detail
│   │
│   └── Incidents (sự kiện bất ngờ)
│       ├── Incident List
│       ├── Create Incident
│       └── Incident Detail
│
├── AI Decision Center
│   │
│   ├── Prediction
│   │   ├── Energy Consumption
│   │   ├── Flight Duration
│   │   └── Flight Performance
│   │
│   ├── Explainability
│   │   ├── SHAP
│   │   ├── Feature Importance
│   │   ├── Global Explanation
│   │   └── Local Explanation
│   │
│   ├── Recommendation
│   │   ├── Mission Planning
│   │   ├── Maintenance
│   │   ├── Battery Health
│   │   ├── Payload Optimisation
│   │   ├── Weather Alert
│   │   ├── Risk Assessment
│   │   └── Operator Assignment
│   │
│   └── What-if Simulation
│       ├── Change Payload
│       ├── Change Altitude
│       ├── Change Speed
│       ├── Change Weather
│       └── Compare Routes
│
├── Analytics
│   ├── Operational KPI
│   ├── Energy Trend
│   ├── Route Comparison
│   └── Historical Analysis
│
├── Reports
│
├── Notifications
│
└── Profile


Technician
│
├── Dashboard
│   ├── Fleet Status
│   ├── Battery Status
│   ├── Maintenance Alerts
│   └── Drone Availability
│
├── Fleet
│   │
│   ├── Drones
│   │   ├── Drone List
│   │   └── Drone Detail
│   │       ├── Drone Information
│   │       ├── Status
│   │       ├── Utilization
│   │       ├── Flight History
│   │       └── Maintenance History
│   │
│   └── Batteries
│       ├── Battery List
│       └── Battery Detail
│           ├── Battery Information
│           └── Replacement History
│
├── Maintenance
│   ├── Overview
│   ├── Maintenance Alerts
│   ├── Work Orders
│   └── Work Order Detail
│       ├── Inspection (sự giám định)
│       ├── Diagnosis
│       ├── Action
│       └── Spare Parts
│
├── Incidents
│   └── Drone-related Incidents
│
├── Reports
│
├── Notifications
│
└── Profile


Admin
│
├── Dashboard
│   ├── Revenue
│   │   ├── Total Revenue
│   │   ├── Revenue Today
│   │   ├── Revenue This Month
│   │   ├── Paid Orders
│   │   ├── Pending Payments
│   │   └── Refunds
│   │
│   ├── System KPI
│   ├── User Statistics
│   ├── Mission Statistics
│   ├── Fleet Statistics
│   ├── Battery Statistics
│   ├── AI Statistics
│   └── System Alerts
│
├── Users
│   ├── User List
│   ├── User Detail
│   └── Roles & Permissions
│
├── Audit
│   └── Audit Logs
│
├── Data Governance
│   ├── Overview
│   ├── Data Quality
│   │   ├── Missing Data
│   │   ├── Duplicate
│   │   ├── Outlier
│   │   └── Validity
│   ├── Data Lineage
│   ├── Data Versioning
│   └── Data Retention
│
├── System
│   ├── Settings
│   ├── Notifications
│   └── Backup
│
└── Profile -->

```

---

## 🎭 Phân quyền (RBAC)

| Role | Mô tả |
|------|-------|
| **Admin** | Quản lý hệ thống: thống kê doanh thu, tài khoản, cấu hình, audit logs và data governance |
| **Operator** | Quản lý và duyệt/hủy chuyến bay (kích hoạt hoàn tiền), theo dõi log bay, xem AI phân tích |
| **Technician** | Quản lý thiết bị vật lý: Drone, Pin, lịch bảo trì |
| **Customer** | Tạo chuyến bay, thanh toán trực tuyến, theo dõi tiến trình và xuất báo cáo (Mission Report) |

---
* Chạy "python app\main.py"
* Rồi vào "http://127.0.0.1:8000/docs" để xem và kiểm tra APIs
* APIs dev/db là những api có thể tương tác trực tiếp với cloud database postgresql. Nên đừng đụng vô, chỉ sử dụng
