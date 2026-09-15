import smtplib
from email.mime.text import MIMEText
from app.core.config import settings


def send_reset_email(to_email: str, reset_link: str):
    body = f"""
    <h3>Đặt lại mật khẩu DroneOptAI</h3>
    <p>Nhấn vào link bên dưới để đặt lại mật khẩu (hết hạn sau 15 phút):</p>
    <a href="{reset_link}">{reset_link}</a>
    <p>Nếu bạn không yêu cầu, hãy bỏ qua email này.</p>
    """
    msg = MIMEText(body, "html")
    msg["Subject"] = "Đặt lại mật khẩu DroneOptAI"
    msg["From"] = settings.GMAIL_USER
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(settings.GMAIL_USER, settings.GMAIL_APP_PASSWORD)
        smtp.sendmail(settings.GMAIL_USER, to_email, msg.as_string())
