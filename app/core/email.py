import httpx
from app.core.config import settings


def send_reset_email(to_email: str, reset_link: str):
    body = f"""
    <h3>Đặt lại mật khẩu DroneOptAI</h3>
    <p>Nhấn vào link bên dưới để đặt lại mật khẩu (hết hạn sau 15 phút):</p>
    <a href="{reset_link}">{reset_link}</a>
    <p>Nếu bạn không yêu cầu, hãy bỏ qua email này.</p>
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
            "subject": "Đặt lại mật khẩu DroneOptAI",
            "htmlContent": body,
        },
        timeout=10,
    )
    response.raise_for_status()


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
            ">Theo dõi đơn hàng</a>
        </p>
    """ if tracking_url else ""

    mission_line = f"<p><b>Mã chuyến bay:</b> {mission_code}</p>" if mission_code else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #2563eb; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">🚁 DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            <h2 style="color: #111827;">Đơn hàng đang được giao!</h2>
            <p>Xin chào,</p>
            <p>Đơn hàng của bạn đang được drone giao đến địa chỉ đích.</p>
            <p><b>Mã đơn hàng:</b> {order_id}</p>
            {mission_line}
            {tracking_section}
            <p style="color: #6b7280; font-size: 13px; margin-top: 24px;">
                Nếu bạn có thắc mắc, vui lòng liên hệ hỗ trợ qua email này.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Hệ thống giao hàng bằng drone
        </div>
    </div>
    """

    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": settings.BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "sender": {"name": "DroneOptAI", "email": settings.EMAIL_FROM},
                "to": [{"email": to_email}],
                "subject": f"[DroneOptAI] Đơn hàng {order_id} đang được giao 🚁",
                "htmlContent": html_body,
            },
            timeout=10,
        )
        response.raise_for_status()
        print(f"[EMAIL] Gửi thành công đến {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL] Lỗi gửi email: {e}")
        return False


async def send_hub_arrival_email(
    to_email: str,
    order_id: str,
    hub_name: str | None = None,
    frontend_url: str | None = None,
) -> bool:
    """
    Gửi email thông báo đơn hàng đã đến trạm hub đích, chờ khách đến nhận.
    """
    hub_line = f"<p><b>Trạm nhận hàng:</b> {hub_name}</p>" if hub_name else ""
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
            ">Xem chi tiết đơn hàng</a>
        </p>
    """ if tracking_url else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: #16a34a; padding: 20px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">✅ DroneOptAI</h1>
        </div>
        <div style="padding: 24px; background-color: #f9fafb; border: 1px solid #e5e7eb;">
            <h2 style="color: #111827;">Đơn hàng đã đến nơi!</h2>
            <p>Xin chào,</p>
            <p>Drone đã giao đơn hàng của bạn đến trạm đích thành công. Vui lòng đến nhận hàng sớm.</p>
            <p><b>Mã đơn hàng:</b> {order_id}</p>
            {hub_line}
            {tracking_section}
            <p style="color: #dc2626; font-size: 13px; margin-top: 12px;">
                ⚠️ Đơn hàng sẽ bị huỷ nếu không được nhận trong vòng 5 ngày.
            </p>
            <p style="color: #6b7280; font-size: 13px; margin-top: 12px;">
                Nếu bạn có thắc mắc, vui lòng liên hệ hỗ trợ qua email này.
            </p>
        </div>
        <div style="padding: 12px; text-align: center; color: #9ca3af; font-size: 12px;">
            DroneOptAI — Hệ thống giao hàng bằng drone
        </div>
    </div>
    """

    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": settings.BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "sender": {"name": "DroneOptAI", "email": settings.EMAIL_FROM},
                "to": [{"email": to_email}],
                "subject": f"[DroneOptAI] Đơn hàng {order_id} đã đến trạm nhận hàng 📦",
                "htmlContent": html_body,
            },
            timeout=10,
        )
        response.raise_for_status()
        print(f"[EMAIL] Gửi hub arrival thành công đến {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL] Lỗi gửi hub arrival email: {e}")
        return False
