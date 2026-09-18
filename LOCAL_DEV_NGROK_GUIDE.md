# 🚀 Hướng Dẫn Chạy Local & Ghép API (BE & FE)

## 1. Dành cho Backend (Người chạy Server)

Nhiệm vụ của BE là khởi động server FastAPI và mở Ngrok để tạo Public URL cho FE gọi tới.

### Bước 1.1: Khởi động Server Local
Mở terminal tại thư mục chứa code Backend , kích hoạt môi trường ảo và chạy Uvicorn:

```bash
# Kích hoạt venv (Windows)
.\venv\Scripts\activate

# Cài đặt thư viện nếu chưa có
pip install -r requirements.txt

# Khởi chạy server FastAPI (mặc định chạy ở port 8000)
python app/main/py
```
👉 Lúc này API nội bộ chạy ở: `http://localhost:8000`

### Bước 1.2: Bật Ngrok để tạo Public URL
*Lưu ý: Bạn phải đăng ký tài khoản Ngrok và cài đặt authtoken trước.*

Mở một cửa sổ Terminal **MỚI** (vẫn giữ terminal chạy Uvicorn ở trên) và gõ:
```bash
ngrok http 8000
```
Bạn sẽ thấy dòng `Forwarding https://xxxx-xxxx.ngrok-free.app -> http://localhost:8000`. 
👉 **Copy cái link `https://xxxx-xxxx.ngrok-free.app` này và gửi cho team FE.**

*(Để tắt ngrok, bấm `Ctrl + C` ở terminal ngrok)*

---

## 2. Dành cho Frontend (Người gọi API)

Khi nhận được link API từ team BE (ví dụ `https://abcd.ngrok-free.app`), team FE cần làm theo các bước sau để gọi API thành công:
link tổng hợp APIs: `https://abcd.ngrok-free.app/docs`

### Bước 2.1: Đổi Base URL
Vào file `.env` hoặc file cấu hình axios của dự án Frontend, đổi `BASE_URL` thành link ngrok vừa nhận.

### Bước 2.2: BẮT BUỘC - Bypass màn hình cảnh báo của Ngrok
**Vấn đề:** Do BE đang dùng ngrok bản miễn phí, khi FE gửi request tới, ngrok sẽ chặn lại bằng một màn hình HTML yêu cầu xác nhận "Visit Site". Điều này sẽ làm code FE bị văng lỗi không parse được JSON (CORS error hoặc Unexpected token `<` in JSON).

**Cách giải quyết:** Thêm header `ngrok-skip-browser-warning` vào TẤT CẢ các request gọi API (thường cấu hình trong Axios instance hoặc Fetch API).

**Ví dụ cấu hình với Axios:**
```javascript
import axios from 'axios';

const api = axios.create({
  baseURL: 'https://abcd.ngrok-free.app', // Link BE gửi
  headers: {
    'Content-Type': 'application/json',
    'ngrok-skip-browser-warning': '69420' // QUAN TRỌNG: Thêm dòng này để bỏ qua màn hình chặn
  }
});

export default api;
```

---

## ⚠️ 3. Đánh Giá Ngrok: Điểm Lợi & Điểm Hại (Cả Team Cần Đọc)

Do đang xài hàng Miễn Phí (Ngrok Free Tier) thay vì Deploy lên Cloud (như Render.com), team cần hiểu rõ lợi ích và hạn chế của nó.

### Những Điểm Lợi (Ưu điểm so với server Cloud như Render)
1. **Không có độ trễ Deploy (Zero Deploy Time):** Code xong lưu lại (`Ctrl + S`), Uvicorn tự reload là FE có thể gọi API ngay lập tức qua link ngrok. Không phải push code lên Github rồi mất 5-10 phút chờ build và deploy như trên Render.
2. **Dễ dàng Debug & Đọc Log trực tiếp:** Bất kỳ lỗi nào xảy ra khi FE gọi API, BE có thể nhìn thấy log lỗi (print, error traceback) hoặc dùng breakpoint debugger ngay trên màn hình terminal của mình theo thời gian thực.
3. **Sử dụng Local DB:** Có thể tận dụng luôn Database dưới local của máy BE để test nhánh tính năng mới mà không sợ làm hỏng Data trên Cloud.
4. **Không bị "Ngủ đông" (Cold Start):** Các server miễn phí như Render sẽ "ngủ" nếu không ai truy cập sau 15 phút, gọi API lần đầu phải chờ rất lâu (30s - 1 phút) để server thức dậy. Với ngrok, API phản hồi ngay lập tức vì code chạy trực tiếp trên máy BE.

### Những Điểm Hại (Hạn chế & Lưu ý quan trọng)

1. **Link API bị đổi liên tục:** 
   - Ngrok miễn phí sẽ cấp một đường link **mới hoàn toàn** mỗi khi BE tắt terminal bật lại, hoặc bị rớt mạng chớp nhoáng. 
   - **Quy trình chuẩn:** Mỗi lần code, BE phải gửi lại link mới cho FE. FE phải chủ động update link trong `BASE_URL`.

2. **Tự động ngắt (Time-out) sau 2 tiếng:**
   - Ngrok sẽ tự động ngắt kết nối một phiên làm việc (session) sau khoảng 2 tiếng. 
   - FE nếu đang test mà bỗng nhiên thấy request xoay đều (timeout) hoặc báo lỗi kết nối, thì nhắn BE ra terminal tắt ngrok bật lại và gửi link mới.

3. **Chỉ dùng để Dev/Test, KHÔNG dùng lúc Bảo Vệ Đồ Án:** (Đề xuất của gemini)
   - Việc ngrok đổi link liên tục và tự ngắt kết nối là **CỰC KỲ RỦI RO** khi đi bảo vệ đồ án trước hội đồng.
   - **Giải pháp lúc bảo vệ:**
     - Tốt nhất: Deploy Backend lên server Cloud miễn phí (Render.com) để có link cố định vĩnh viễn.
     - Thay thế: FE và BE chạy chung trên 1 máy tính và kết nối thẳng qua `http://localhost:8000`.
     - Thay thế 2: Sử dụng `Cloudflare Tunnels` thay cho Ngrok.

4. **Giới hạn gọi API (Rate Limit):**
   - Không spam API liên tục (giới hạn ~40-120 requests/phút). Nếu không cả team sẽ bị ăn lỗi HTTP 429 (Too Many Requests).
