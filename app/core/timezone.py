from datetime import datetime
from zoneinfo import ZoneInfo

# Múi giờ chuẩn Việt Nam (GMT+7)
VN_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

def get_vn_datetime() -> datetime:
    """Trả về datetime hiện tại theo múi giờ Việt Nam (+7)."""
    return datetime.now(VN_TIMEZONE)

def get_vn_naive_datetime() -> datetime:
    """
    Trả về datetime hiện tại theo giờ Việt Nam nhưng không kèm tzinfo (naive).
    Phù hợp cho các cột PostgreSQL kiểu 'TIMESTAMP WITHOUT TIME ZONE'.
    """
    return datetime.now(VN_TIMEZONE).replace(tzinfo=None)

def to_vn_datetime(dt: datetime | None) -> datetime | None:
    """Chuyển đổi một đối tượng datetime sang giờ Việt Nam."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Nếu naive datetime, coi như nó đã là giờ VN hoặc gán timezone
        return dt.replace(tzinfo=VN_TIMEZONE)
    return dt.astimezone(VN_TIMEZONE)
