# 🤖 Hướng Dẫn Tích Hợp Chatbot AI Đa Vai Trò & Đồng Bộ FE/BE (v1.1)

Tài liệu này giải thích cấu trúc module hóa theo vai trò (**Role-based Module Structure**), đồng bộ **Canonical Business Rules (FE/BE Sync Spec v1.1)**, cơ chế bảo mật Ownership và hướng dẫn kết nối Frontend với hệ thống **Chatbot AI (Google Gemini Agent)** trong dự án **DroneOptAI**.

---

## 📁 1. Cấu Trúc Module Chuẩn Hóa Theo Từng Role

```text
app/modules/chatbot/
├── __init__.py
├── schemas.py              # Schemas Pydantic v1.1 (ChatMessageRequest, ChatMessageResponse, ChatActionItem, ChatMeta)
├── service.py              # Shared AI Engine: Gemini Agentic Loop, Conversation Store, Rate Limiter, Action Extraction
│
├── customer/               # 🟢 Dành cho Khách hàng (FE/BE Sync Spec v1.1)
│   ├── __init__.py
│   ├── knowledge/
│   │   └── customer_help.json  # Nguồn chuẩn: Package tiers, Pricing, Delivery modes, 12 Help topics & Undefined policies
│   ├── reference_service.py    # CustomerReferenceService, CustomerHelpService, CustomerStatusService
│   ├── prompts.py              # System prompt: 10 Mandatory Rules chống bịa đặt (No-Hallucination)
│   ├── tools.py                # 18 Canonical Tools (8 Public/Reference + 10 Authenticated/Ownership)
│   └── router.py               # Prefix: /api/v1/customer/chatbot/...
│
├── operator/               # 🔵 Dành cho Nhân viên điều phối bay
│   ├── __init__.py
│   ├── prompts.py
│   ├── tools.py            # Tools: get_active_missions, get_operator_kpi, get_recent_incidents, search_mission_by_id
│   └── router.py           # Prefix: /api/v1/operator/chatbot/... (Yêu cầu quyền OPERATOR / ADMIN)
│
├── technician/             # 🟠 Dành cho Kỹ thuật viên bảo trì
│   ├── __init__.py
│   ├── prompts.py
│   ├── tools.py            # Tools: get_fleet_status, get_battery_status, get_pending_work_orders
│   └── router.py           # Prefix: /api/v1/technician/chatbot/... (Yêu cầu quyền TECHNICIAN / ADMIN)
│
└── admin/                  # 🔴 Dành cho Quản trị viên
    ├── __init__.py
    ├── prompts.py
    ├── tools.py            # Tools: get_system_executive_summary, get_user_role_counts
    └── router.py           # Prefix: /api/v1/admin/chatbot/... (Yêu cầu quyền ADMIN)
```

---

## 📡 2. Danh Sách API Endpoints (Chatbot & Customer Reference/Help)

### 2.1. Endpoints Chatbot Đa Vai Trò
| Role | Endpoint Chat | Endpoint Gợi ý nhanh | Quyền truy cập (RBAC) |
| :--- | :--- | :--- | :--- |
| 🟢 **Customer** | `POST /api/v1/customer/chatbot/chat` | `GET /api/v1/customer/chatbot/quick-questions` | Public cho câu hỏi chung; bắt buộc Bearer JWT cho 10 tools cá nhân |
| 🔵 **Operator** | `POST /api/v1/operator/chatbot/chat` | `GET /api/v1/operator/chatbot/quick-questions` | Bắt buộc `OPERATOR` hoặc `ADMIN` |
| 🟠 **Technician** | `POST /api/v1/technician/chatbot/chat` | `GET /api/v1/technician/chatbot/quick-questions` | Bắt buộc `TECHNICIAN` hoặc `ADMIN` |
| 🔴 **Admin** | `POST /api/v1/admin/chatbot/chat` | `GET /api/v1/admin/chatbot/quick-questions` | Bắt buộc `ADMIN` |

### 2.2. Endpoints Reference, Help & Customer Orders (Đồng bộ FE/BE trong module `orders`)
| Endpoint | Auth | Mục đích |
| :--- | :--- | :--- |
| `GET /api/v1/customer/orders/reference/package-rules` | Public | 8 loại hàng hóa & 4 cỡ kiện hàng S/M/L/XL (tối đa 10kg) |
| `GET /api/v1/customer/orders/reference/pricing-rules` | Public | Bảng giá Base/Distance/Payload theo tier & % phụ phí |
| `GET /api/v1/customer/orders/reference/delivery-modes` | Public | Mô tả `SCHEDULED` (0%) và `EXPRESS` (+25% Core Fee) |
| `GET /api/v1/customer/orders/help/topics` | Public | Danh sách 12 chủ đề Help & Policies chính thức + Delivery Flow |
| `GET /api/v1/customer/orders/help/topics/{topic}` | Public | Chi tiết chủ đề hoặc trả về `POLICY_NOT_DEFINED` |
| `GET /api/v1/customer/orders` | Bearer JWT | Danh sách đơn hàng của khách hàng hiện tại |
| `GET /api/v1/customer/orders/{order_id}` | Bearer JWT + Ownership | Chi tiết đơn hàng của khách hàng hiện tại |

---

## 🛠️ 3. Danh Sách 18 Canonical Tools Của Customer Chatbot

- **8 Public/Reference Tools**:
  1. `get_package_rules`
  2. `recommend_package_size`
  3. `get_pricing_rules`
  4. `estimate_shipping_fee`
  5. `get_delivery_modes`
  6. `list_hubs`
  7. `resolve_hubs_by_area`
  8. `get_help_policy`
- **10 Personal/Authenticated Tools (Bắt buộc JWT & kiểm tra `order.customer_id == current_user.id`)**:
  9. `get_my_orders`
  10. `get_latest_order`
  11. `get_order_detail`
  12. `get_customer_delivery_status`
  13. `get_payment_status`
  14. `get_handover_status`
  15. `get_delivery_eta`
  16. `check_cancellation_eligibility`
  17. `get_my_notifications`
  18. `get_profile`

---

## 📦 4. Chuẩn Response Contract v1.1 & Rate Limit (HTTP 429)

### 4.1. Response thành công (`HTTP 200`)
```json
{
  "conversation_id": "chat_abc123",
  "reply": "Đơn ORD-0001 đã thanh toán và đang chờ bạn mang hàng tới origin hub.",
  "tools_called": ["get_latest_order"],
  "data": {
    "type": "order_status",
    "order_id": "ORD-0001",
    "payment_status": "PAID",
    "customer_status": "awaiting_package_dropoff"
  },
  "actions": [
    {
      "type": "OPEN_ORDER",
      "label": "View delivery",
      "href": "/customer/deliveries/ORD-0001"
    }
  ],
  "meta": {
    "request_id": "req_a1b2c3d4e5"
  }
}
```

### 4.2. Response khi vượt giới hạn Rate Limit (`HTTP 429`)
```json
{
  "code": "CHAT_RATE_LIMITED",
  "message": "Assistant is temporarily busy.",
  "retry_after_seconds": 30
}
```
