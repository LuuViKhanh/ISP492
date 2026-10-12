# 🤖 Hướng Dẫn Tích Hợp Chatbot AI Đa Vai Trò (Multi-Role DroneOptAI Assistant)

Tài liệu này giải thích cấu trúc module hóa theo vai trò (**Role-based Module Structure**), nguyên lý hoạt động, lưu ý về **giới hạn tải đồng thời (Concurrency & Rate Limits)** và hướng dẫn kết nối Frontend với hệ thống **Chatbot AI (Google Gemini Agent)** trong dự án **DroneOptAI**.

---

## 📁 1. Cấu Trúc Module Chuẩn Hóa Theo Từng Role

```text
app/modules/chatbot/
├── __init__.py
├── schemas.py              # Schemas Pydantic dùng chung (ChatMessageRequest, ChatMessageResponse, QuickQuestions)
├── service.py              # Shared AI Engine: Điều phối Gemini Agentic Loop và gọi Tool bất đồng bộ (Async)
│
├── customer/               # 🟢 Dành cho Khách hàng
│   ├── __init__.py
│   ├── prompts.py          # System prompt: Trợ lý tư vấn, tra cứu đơn, báo giá ship
│   ├── tools.py            # Tools: lookup_order, estimate_shipping_fee, list_hubs, get_my_recent_orders
│   └── router.py           # Prefix: /api/v1/customer/chatbot/...
│
├── operator/               # 🔵 Dành cho Nhân viên điều phối bay
│   ├── __init__.py
│   ├── prompts.py          # System prompt: Flight Operations Copilot
│   ├── tools.py            # Tools: get_active_missions, get_operator_kpi, get_recent_incidents, search_mission_by_id
│   └── router.py           # Prefix: /api/v1/operator/chatbot/... (Yêu cầu quyền OPERATOR / ADMIN)
│
├── technician/             # 🟠 Dành cho Kỹ thuật viên bảo trì
│   ├── __init__.py
│   ├── prompts.py          # System prompt: Fleet Maintenance Copilot
│   ├── tools.py            # Tools: get_fleet_status, get_battery_status, get_pending_work_orders
│   └── router.py           # Prefix: /api/v1/technician/chatbot/... (Yêu cầu quyền TECHNICIAN / ADMIN)
│
└── admin/                  # 🔴 Dành cho Quản trị viên
    ├── __init__.py
    ├── prompts.py          # System prompt: Executive Management Copilot
    ├── tools.py            # Tools: get_system_executive_summary, get_user_role_counts
    └── router.py           # Prefix: /api/v1/admin/chatbot/... (Yêu cầu quyền ADMIN)
```

---

## 📡 2. Danh Sách API Endpoints Theo Role

Tất cả các endpoint đều hỗ trợ chuẩn OpenAPI Swagger (`/docs`) với icon màu phân loại vai trò tự động:

| Role | Endpoint Chat | Endpoint Gợi ý nhanh | Quyền truy cập (RBAC) |
| :--- | :--- | :--- | :--- |
| 🟢 **Customer** | `POST /api/v1/customer/chatbot/chat` | `GET /api/v1/customer/chatbot/quick-questions` | Public hoặc Khách đã đăng nhập (Token tùy chọn) |
| 🔵 **Operator** | `POST /api/v1/operator/chatbot/chat` | `GET /api/v1/operator/chatbot/quick-questions` | Bắt buộc `OPERATOR` hoặc `ADMIN` |
| 🟠 **Technician** | `POST /api/v1/technician/chatbot/chat` | `GET /api/v1/technician/chatbot/quick-questions` | Bắt buộc `TECHNICIAN` hoặc `ADMIN` |
| 🔴 **Admin** | `POST /api/v1/admin/chatbot/chat` | `GET /api/v1/admin/chatbot/quick-questions` | Bắt buộc `ADMIN` |

---

## 🔑 3. Cấu Hình Biến Môi Trường (.env)

Thêm API key vào file [`.env`](file:///D:/works/Đồ%20án%20kỳ%202/project/New%20folder/Drone-backend/.env):

```env
# Google Gemini AI Chatbot
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxx
GEMINI_MODEL=gemini-3.8-flash
```

---

## ⚠️ 4. Lưu Ý Quan Trọng Về Tải Đồng Thời & Giới Hạn Rate Limit (Concurrency & Limitations)

### 4.1. Bản chất vấn đề khi nhiều người hỏi cùng lúc:
1. **Phía Server FastAPI (Xử lý rất tốt ✅):**
   - Được thiết kế theo cơ chế bất đồng bộ (**Asynchronous ASGI non-blocking**), máy chủ backend có khả năng nhận và duy trì hàng ngàn kết nối đồng thời mà không bị treo.
2. **Phía Google Gemini API (Điểm nghẽn hạn mức ⚠️):**
   - **Gói Free Tier (Miễn phí):** Google áp dụng giới hạn nghiêm ngặt **5 - 15 requests/phút (RPM)**.
   - Khi một câu hỏi kích hoạt Tool (ví dụ tính phí hoặc tra cứu đơn), hệ thống gọi Gemini **2 lần** (Lần 1 để phân tích gọi Tool, Lần 2 để tổng hợp câu trả lời).
   - Do đó, nếu có từ **3 đến 5 người chat dồn dập trong cùng 1 phút**, Google sẽ trả về lỗi:  
     `429 RESOURCE_EXHAUSTED: Quota exceeded for metric generate_content_free_tier_requests`.

### 4.2. Cơ chế xử lý đã tích hợp sẵn trong mã nguồn:
- **Tự động thử lại (Retry Loop):** Hàm `call_gemini` trong [`app/modules/chatbot/service.py`](file:///D:/works/Đồ%20án%20kỳ%202/project/New%20folder/Drone-backend/app/modules/chatbot/service.py) đã được cài đặt cơ chế tự động thử lại tối đa 3 lần (sau khi nghỉ 1.5s) nếu gặp mã `503 UNAVAILABLE` hoặc `429 RESOURCE_EXHAUSTED` tạm thời.
- **Bắt lỗi an toàn (Graceful Error Handling):** Nếu vượt quá số lần thử lại, server không bị crash mà trả về thông báo lịch sự cho người dùng thử lại sau giây lát.

### 4.3. Phương án mở rộng cho Demo Đồ Án / Production:
- **Phương án 1 (Khuyên dùng): Bật gói Pay-As-You-Go**
  - Gắn thẻ thanh toán vào Google AI Studio.
  - Hạn mức sẽ tăng ngay từ 15 RPM lên **1,000 - 4,000 requests/phút** (thoải mái cho cả hội đồng và người dùng cùng test).
  - Chi phí của `gemini-3.8-flash` cực kỳ rẻ (~$0.075 / 1 triệu tokens, nạp 20,000 - 50,000 VNĐ là dùng thoải mái cho cả kỳ đồ án).
- **Phương án 2: Xoay vòng Key (Key Rotation)**
  - Nếu dùng Free Tier, các thành viên trong nhóm có thể tạo 3-4 API keys từ các tài khoản Google khác nhau để cấu hình xoay vòng (Round-Robin), nhân gấp 3-4 lần hạn mức miễn phí.
- **Phương án 3: Bổ sung Cache (Redis / In-Memory Cache)**
  - Lưu cache câu trả lời cho các câu hỏi tĩnh/lặp lại (như danh sách các trạm Hub, biểu phí cơ bản).
- **Phương án 4: Tăng số lượng Worker của Uvicorn**
  - Chạy `uvicorn app.main:app --workers 4 --port 8000` để tận dụng đa nhân CPU khi chịu tải cao.

---

## 💻 5. Mẫu Gọi API Từ Frontend (Ví dụ: Customer Chatbot)

```javascript
import axios from 'axios';

// Gửi tin nhắn đến Customer Chatbot
export const chatWithCustomerBot = async (message, history = []) => {
  const token = localStorage.getItem('token');
  const headers = token ? { Authorization: `Bearer ${token}` } : {};

  try {
    const response = await axios.post(
      'http://localhost:8000/api/v1/customer/chatbot/chat',
      { message, history },
      { headers }
    );
    return response.data;
  } catch (error) {
    if (error.response && error.response.status === 429) {
      return {
        reply: "Trợ lý ảo hiện đang có nhiều lượt truy cập cùng lúc. Bạn vui lòng đợi khoảng vài chục giây rồi thử lại nhé! ⏳",
        tools_called: [],
        data: null
      };
    }
    throw error;
  }
};
```
