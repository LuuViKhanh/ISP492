# Payment Integration Guide — PayOS

Base URL: `https://isp492.onrender.com/api/v1`  
Auth: `Authorization: Bearer <token>`

---

## 1. Flow tổng quan

```
1. FE gọi POST /payments/orders/{order_id}/checkout
   → Nhận checkout_url + qr_code

2. FE redirect user đến checkout_url (hoặc hiện QR)
   → User quét QR / thanh toán trên trang PayOS

3. Sau khi thanh toán xong:
   → PayOS gọi webhook về BE (tự động)
   → BE cập nhật payment_status = PAID

4. PayOS redirect user về:
   → Thành công: {FRONTEND_URL}/auth/google/callback?...  (return URL)
   → Huỷ:        {FRONTEND_URL}/payment/cancel?order_id=...

5. FE gọi GET /payments/orders/{order_id}/status để confirm
```

---

## 2. Endpoints

### `POST /payments/orders/{order_id}/checkout`
Tạo link thanh toán PayOS.

**Request:**
```http
POST /api/v1/payments/orders/ORD-001/checkout
Authorization: Bearer <customer_token>
```

**Response 200:**
```json
{
  "order_id": "ORD-001",
  "payment_order_code": 9325539,
  "checkout_url": "https://pay.payos.vn/web/abc123",
  "qr_code": "00020101...",
  "amount": 50000,
  "payment_status": "PENDING"
}
```

**Lưu ý:**
- `delivery_fee` phải được set trước khi gọi (do Operator set)
- Nếu đã có link PENDING → trả lại link cũ, không tạo mới
- Nếu đã PAID → trả về 400

---

### `GET /payments/orders/{order_id}/status`
Kiểm tra trạng thái thanh toán. FE poll endpoint này sau khi user thanh toán.

**Response 200:**
```json
{
  "order_id": "ORD-001",
  "payment_status": "PAID",
  "paid_at": "2026-10-05T13:08:41",
  "payment_transaction_id": "TF230204212323",
  "amount": 50000
}
```

**Các giá trị `payment_status`:**

| Status | Ý nghĩa |
|--------|---------|
| `UNPAID` | Chưa tạo link thanh toán |
| `PENDING` | Đã tạo link, chờ user thanh toán |
| `PAID` | Thanh toán thành công ✅ |
| `FAILED` | Thanh toán thất bại |
| `CANCELLED` | User huỷ hoặc BE huỷ |
| `REFUNDED` | Đã hoàn tiền |

---

### `POST /payments/orders/{order_id}/cancel`
Huỷ payment link (chỉ khi chưa PAID).

**Response 200:**
```json
{
  "message": "Đã huỷ payment link",
  "order_id": "ORD-001"
}
```

---

## 3. Cách tích hợp FE

### Bước 1 — Tạo checkout

```javascript
async function createCheckout(orderId, token) {
  const res = await fetch(`/api/v1/payments/orders/${orderId}/checkout`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json();

  if (res.ok) {
    // Option A: Redirect sang trang PayOS
    window.location.href = data.checkout_url;

    // Option B: Hiện QR code trong popup
    showQRCode(data.qr_code);
  }
}
```

### Bước 2 — Xử lý Return URL

PayOS redirect về `{FRONTEND_URL}/payment/success` sau khi thanh toán. FE cần tạo page này:

```javascript
// /payment/success
const params = new URLSearchParams(window.location.search);
const orderId   = params.get('order_id');
const status    = params.get('status');   // "PAID" | "CANCELLED"
const code      = params.get('code');     // "00" = thành công
const orderCode = params.get('orderCode');

if (status === 'PAID' && code === '00') {
  // Poll để confirm
  await pollPaymentStatus(orderId);
} else {
  showError('Thanh toán thất bại hoặc bị huỷ');
}
```

### Bước 3 — Poll trạng thái

```javascript
async function pollPaymentStatus(orderId, token, maxRetries = 10) {
  for (let i = 0; i < maxRetries; i++) {
    const res = await fetch(`/api/v1/payments/orders/${orderId}/status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json();

    if (data.payment_status === 'PAID') {
      // Thanh toán thành công → redirect về order detail
      window.location.href = `/orders/${orderId}`;
      return;
    }

    if (data.payment_status === 'FAILED' || data.payment_status === 'CANCELLED') {
      showError('Thanh toán không thành công');
      return;
    }

    // Chờ 2s rồi thử lại
    await new Promise(r => setTimeout(r, 2000));
  }
  showError('Timeout — vui lòng kiểm tra lại đơn hàng');
}
```

---

## 4. Routes FE cần tạo

| Route | Mô tả |
|-------|-------|
| `/payment/success` | PayOS redirect về sau khi thanh toán thành công |
| `/payment/cancel` | PayOS redirect về khi user huỷ thanh toán |

Query params PayOS gửi về:
```
?order_id=ORD-001
&code=00
&id=7a99f50657fc4b30a0fad797c4ec26b4
&cancel=false
&status=PAID
&orderCode=9325539
```

---

## 5. SQL cần chạy trên DB (1 lần)

```sql
ALTER TABLE orders
ADD COLUMN IF NOT EXISTS delivery_fee FLOAT,
ADD COLUMN IF NOT EXISTS payment_status VARCHAR(20) DEFAULT 'UNPAID',
ADD COLUMN IF NOT EXISTS payment_order_code BIGINT UNIQUE,
ADD COLUMN IF NOT EXISTS payment_transaction_id VARCHAR(100),
ADD COLUMN IF NOT EXISTS payment_checkout_url VARCHAR(500),
ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP;
```

---

## 6. Lưu ý

- Webhook được BE tự đăng ký với PayOS khi server khởi động — FE không cần làm gì
- `delivery_fee` được Operator set, không phải Customer
- Token Customer mới được gọi checkout — Operator/Admin cũng được
- `payment_order_code` là số nguyên dương duy nhất, không phải `order_id`
