"""
app/modules/payments/payos_client.py
─────────────────────────────────────
PayOS client wrapper.
SDK: pip install payos
"""

from app.core.config import settings


def get_payos():
    """Khởi tạo PayOS client với credentials từ config."""
    try:
        from payos import PayOS
    except ImportError:
        raise RuntimeError("payos package chưa được cài. Chạy: pip install payos==0.1.7")
    return PayOS(
        client_id=settings.PAYOS_CLIENT_ID,
        api_key=settings.PAYOS_API_KEY,
        checksum_key=settings.PAYOS_CHECKSUM_KEY,
    )
