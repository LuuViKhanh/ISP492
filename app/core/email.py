import asyncio
import weakref
import httpx
from app.core.config import settings

# [PERF NOTE] KHÔNG dùng httpx.post (blocking I/O) trong các hàm async def gửi email!
# Blocking I/O sẽ làm treo toàn bộ FastAPI event loop tới 10s (ảnh hưởng cả live-tracking và mọi API khác).
# Dùng AsyncClient theo từng event loop (hỗ trợ an toàn cả main uvicorn loop lẫn worker thread chạy asyncio.run).
_BREVO_CLIENTS_BY_LOOP: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = weakref.WeakKeyDictionary()


def _get_brevo_async_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _BREVO_CLIENTS_BY_LOOP.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=10.0)
        _BREVO_CLIENTS_BY_LOOP[loop] = client
    return client


async def _send_brevo_email_async(
    to_email: str,
    subject: str,
    html_body: str,
    log_label: str,
) -> bool:
    """Gửi email bất đồng bộ qua Brevo API mà không làm nghẽn asyncio event loop."""
    try:
        client = _get_brevo_async_client()
        response = await client.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": settings.BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "sender": {"name": "DroneOptAI", "email": settings.EMAIL_FROM},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_body,
            },
        )
        response.raise_for_status()
        print(f"[EMAIL] Gửi {log_label} thành công đến {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL] Lỗi gửi {log_label} email: {e}")
        return False


def send_reset_email(to_email: str, reset_link: str):
    body = f"""
    <h3>Reset Your DroneOptAI Password</h3>
    <p>Click the link below to reset your password (expires in 15 minutes):</p>
    <a href="{reset_link}">{reset_link}</a>
    <p>If you did not request this, please ignore this email.</p>
    """
    response = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": settings.BREVO_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "sender": {"name": "DroneOptAI", "email": settings.EMAIL_FROM},
            "to": [{"email": to_email}],
            "subject": "Reset Your DroneOptAI Password",
            "htmlContent": body,
        },
        timeout=10,
    )
    response.raise_for_status()


async def send_mission_scheduled_email(
    to_email: str,
    order_id: str,
    mission_id: str | None = None,
    frontend_url: str | None = None,
) -> bool:
    """
    Gửi email thông báo đơn hàng đã được lên lịch bay (operator tạo mission).
    """
    tracking_url = f"{frontend_url}/orders/{order_id}/tracking" if frontend_url else ""
    tracking_section = f"""
        <p>
            <a href="{tracking_url}" style="
                display: inline-block;
                padding: 10px 20px;
                background-color: #7c3aed;
                color: #ffffff;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
            ">Track Order</a>
        </p>
    """ if tracking_url else ""

    mission_line = f"<p><b>Mission ID:</b> {mission_id}</p>" if mission_id else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #7c3aed; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">📅 DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            <h2 style="color: #111827;">Your Order Has Been Scheduled for Delivery!</h2>
            <p>Hello,</p>
            <p>Your order has been confirmed and scheduled for a drone delivery mission.</p>
            <p><b>Order ID:</b> {order_id}</p>
            {mission_line}
            <p>The drone will take off once the origin station confirms package drop-off.</p>
            {tracking_section}
            <p style="color: #6b7280; font-size: 13px; margin-top: 24px;">
                If you have any questions, please contact support via this email.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Drone Delivery System
        </div>
    </div>
    """

    return await _send_brevo_email_async(
        to_email=to_email,
        subject=f"[DroneOptAI] Order {order_id} has been scheduled for flight 📅",
        html_body=html_body,
        log_label="mission scheduled",
    )


async def send_delivery_email(
    to_email: str,
    order_id: str,
    mission_code: str | None = None,
    frontend_url: str | None = None,
) -> bool:
    """
    Gửi email thông báo đơn hàng đang được drone giao.
    Trả về True nếu thành công, False nếu lỗi (không throw).
    """
    tracking_url = f"{frontend_url}/orders/{order_id}/tracking" if frontend_url else ""

    tracking_section = f"""
        <p>
            <a href="{tracking_url}" style="
                display: inline-block;
                padding: 10px 20px;
                background-color: #2563eb;
                color: #ffffff;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
            ">Track Order</a>
        </p>
    """ if tracking_url else ""

    mission_line = f"<p><b>Mission Code:</b> {mission_code}</p>" if mission_code else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #2563eb; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">🚁 DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            <h2 style="color: #111827;">Your Order Is Out for Delivery!</h2>
            <p>Hello,</p>
            <p>Your order is currently being delivered by drone to the destination hub.</p>
            <p><b>Order ID:</b> {order_id}</p>
            {mission_line}
            {tracking_section}
            <p style="color: #6b7280; font-size: 13px; margin-top: 24px;">
                If you have any questions, please contact support via this email.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Drone Delivery System
        </div>
    </div>
    """

    return await _send_brevo_email_async(
        to_email=to_email,
        subject=f"[DroneOptAI] Order {order_id} is out for delivery 🚁",
        html_body=html_body,
        log_label="delivery",
    )


async def send_hub_arrival_email(
    to_email: str,
    order_id: str,
    hub_name: str | None = None,
    frontend_url: str | None = None,
) -> bool:
    """
    Gửi email thông báo đơn hàng đã đến trạm hub đích, chờ khách đến nhận.
    """
    hub_line = f"<p><b>Pickup Hub:</b> {hub_name}</p>" if hub_name else ""
    tracking_url = f"{frontend_url}/orders/{order_id}/tracking" if frontend_url else ""
    tracking_section = f"""
        <p>
            <a href="{tracking_url}" style="
                display: inline-block;
                padding: 10px 20px;
                background-color: #16a34a;
                color: #ffffff;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
            ">View Order Details</a>
        </p>
    """ if tracking_url else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #16a34a; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">✅ DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            <h2 style="color: #111827;">Your Order Has Arrived!</h2>
            <p>Hello,</p>
            <p>The drone has successfully delivered your order to the destination hub. Please pick up your package soon.</p>
            <p><b>Order ID:</b> {order_id}</p>
            {hub_line}
            {tracking_section}
            <p style="color: #dc2626; font-size: 13px; margin-top: 12px;">
                ⚠️ Your order will be cancelled if it is not picked up within 5 days.
            </p>
            <p style="color: #6b7280; font-size: 13px; margin-top: 12px;">
                If you have any questions, please contact support via this email.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Drone Delivery System
        </div>
    </div>
    """

    return await _send_brevo_email_async(
        to_email=to_email,
        subject=f"[DroneOptAI] Order {order_id} has arrived at the pickup hub 📦",
        html_body=html_body,
        log_label="hub arrival",
    )


async def send_refund_email(
    to_email: str,
    order_id: str,
    refund_amount: float,
    refund_reason: str,
) -> bool:
    """
    Gửi email thông báo hoàn tiền cho khách hàng gửi hàng (shipper/sender).
    """
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #16a34a; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">💰 DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            <h2 style="color: #111827;">Hoàn tiền đã được xử lý</h2>
            <p>Xin chào,</p>
            <p>Chúng tôi đã xử lý hoàn tiền cho đơn hàng của bạn. Vui lòng xem thông tin chi tiết dưới đây:</p>
            
            <div style="background-color: #ffffff; border: 1px solid #e5e7eb; padding: 16px; margin: 16px 0; border-radius: 6px;">
                <p><b>Mã đơn hàng:</b> {order_id}</p>
                <p><b>Số tiền hoàn:</b> {refund_amount:,.0f} VNĐ</p>
                <p><b>Lý do hoàn tiền:</b> {refund_reason}</p>
            </div>
            
            <p style="color: #4b5563;">Tiền hoàn sẽ được chuyển về tài khoản ngân hàng của bạn trong vòng 1-3 ngày làm việc.</p>
            
            <p style="color: #6b7280; font-size: 13px; margin-top: 12px;">
                Nếu bạn có thắc mắc, vui lòng liên hệ hỗ trợ qua email này.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Hệ thống giao hàng bằng drone
        </div>
    </div>
    """

    return await _send_brevo_email_async(
        to_email=to_email,
        subject=f"[DroneOptAI] Hoàn tiền đơn hàng {order_id} 💰",
        html_body=html_body,
        log_label="refund",
    )


async def send_incident_notification_email(
    to_email: str,
    order_id: str,
    incident_detail: str = "",
    role: str = "sender",  # "sender" hoặc "receiver"
) -> bool:
    """
    Gửi email thông báo sự cố chuyến bay cho khách hàng (sender hoặc receiver).
    """
    if role == "sender":
        role_label = "Khách hàng gửi hàng"
        message_body = f"""
        <p>Xin chào khách hàng gửi hàng,</p>
        <p>Chúng tôi rất tiếc phải thông báo rằng đơn hàng <b>#{order_id}</b> của bạn gặp sự cố kỹ thuật trong quá trình vận chuyển bằng drone.</p>
        <div style="background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 12px; margin: 16px 0;">
            <p><b>Chi tiết sự cố:</b> {incident_detail or 'Đợi xác nhận từ kỹ thuật viên'}</p>
        </div>
        <p>Kiện hàng đã được đưa về kho an toàn và chúng tôi đang sắp xếp lại chuyến bay. Bạn sẽ nhận được thông báo tiếp theo khi chuyến bay được lên lịch lại.</p>
        <p>Cảm ơn bạn đã sử dụng dịch vụ DroneOptAI.</p>
        """
    else:  # receiver
        role_label = "Khách hàng nhận hàng"
        message_body = f"""
        <p>Xin chào khách hàng nhận hàng,</p>
        <p>Chúng tôi rất tiếc phải thông báo rằng đơn hàng <b>#{order_id}</b> gặp sự cố kỹ thuật trong quá trình vận chuyển bằng drone.</p>
        <div style="background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 12px; margin: 16px 0;">
            <p><b>Chi tiết sự cố:</b> {incident_detail or 'Đợi xác nhận từ kỹ thuật viên'}</p>
        </div>
        <p>Kiện hàng đã được đưa về kho an toàn. Chúng tôi sẽ liên hệ với bạn khi chuyến bay được sắp xếp lại và hàng sẽ được gửi đến bạn vào thời gian phù hợp.</p>
        <p>Cảm ơn sự kiên nhẫn của bạn.</p>
        """
    
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #dc2626; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">⚠️ Thông báo sự cố DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            {message_body}
            <p style="color: #6b7280; font-size: 13px; margin-top: 16px;">
                Nếu bạn có thắc mắc, vui lòng liên hệ hỗ trợ qua email này.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Hệ thống giao hàng bằng drone
        </div>
    </div>
    """

    return await _send_brevo_email_async(
        to_email=to_email,
        subject=f"[DroneOptAI] ⚠️ Sự cố chuyến bay — Đơn hàng {order_id}",
        html_body=html_body,
        log_label=f"incident ({role_label})",
    )
