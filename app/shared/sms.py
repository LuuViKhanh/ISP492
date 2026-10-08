"""
app/shared/sms.py
──────────────────
Gửi SMS thông báo qua ESMS.vn (nhà cung cấp SMS Việt Nam).

Đăng ký tại: https://esms.vn
Sau khi đăng ký lấy: API_KEY, SECRET_KEY, Brandname (tên hiển thị)

Docs API: https://esms.vn/api-sms
"""

import httpx
from typing import Optional
from app.core.config import settings


async def send_sms(phone: str, message: str) -> bool:
    """
    Gửi SMS đến số điện thoại.
    Trả về True nếu thành công, False nếu lỗi (không throw).
    
    phone: số điện thoại VN, ví dụ "0901234567" hoặc "+84901234567"
    """
    if not settings.ESMS_API_KEY or not settings.ESMS_SECRET_KEY:
        print(f"[SMS] Chưa cấu hình ESMS_API_KEY — bỏ qua gửi SMS đến {phone}")
        return False

    # Chuẩn hoá số điện thoại về dạng 84xxxxxxxxx
    phone_normalized = phone.strip()
    if phone_normalized.startswith("0"):
        phone_normalized = "84" + phone_normalized[1:]
    elif phone_normalized.startswith("+84"):
        phone_normalized = "84" + phone_normalized[3:]

    payload = {
        "ApiKey":     settings.ESMS_API_KEY,
        "SecretKey":  settings.ESMS_SECRET_KEY,
        "Phone":      phone_normalized,
        "Content":    message,
        "SmsType":    2,   # 2 = số ngẫu nhiên (không cần đăng ký Brandname)
        "IsUnicode":  0,   # 0 = không dấu (an toàn hơn với SMS type 2)
        "Brandname":  "",  # ESMS yêu cầu field này ngay cả SmsType=2, set empty để dùng số random
    }
    print(f"[SMS] DEBUG payload: {payload}")  # DEBUG

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://rest.esms.vn/MainService.svc/json/SendMultipleMessage_V4_post_json/",
                json=payload,
            )
            data = resp.json()
            code = data.get("CodeResult") or data.get("code")
            if str(code) == "100":
                print(f"[SMS] Gửi thành công đến {phone}")
                return True
            else:
                print(f"[SMS] Lỗi ESMS: {data}")
                return False
    except Exception as e:
        print(f"[SMS] Exception khi gửi SMS: {e}")
        return False


async def send_order_flying_sms(
    phone: str,
    order_code: str,
    mission_code: Optional[str] = None,
    frontend_url: Optional[str] = None,
) -> bool:
    """
    Gửi SMS thông báo đơn hàng đang được drone giao.
    Giống tin nhắn Viettelpost: thông tin ngắn gọn + link theo dõi.
    """
    tracking_url = f"{frontend_url}/orders/{order_code}/tracking" if frontend_url else ""

    message = (
        f"DroneOptAI: Don hang {order_code} dang duoc giao bang drone. "
    )
    if tracking_url:
        message += f"Theo doi tai: {tracking_url}"
    else:
        message += "Vui long mo app de theo doi."

    return await send_sms(phone, message)
