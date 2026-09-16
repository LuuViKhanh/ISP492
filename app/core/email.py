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
