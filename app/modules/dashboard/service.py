from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.missions.models import MissionStatus

# Các trạng thái coi là "đã hoàn tất" (dùng cho doanh thu & tỉ lệ thành công)
COMPLETED_STATUSES = [MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED]


def now_naive() -> datetime:
    """Thời điểm hiện tại dạng naive UTC (nhất quán với toàn bộ codebase)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def group_counts(db: AsyncSession, column) -> dict[str, int]:
    """
    Đếm số bản ghi theo một cột (GROUP BY). Hỗ trợ cột Enum (lấy .value) lẫn chuỗi thường.
    Giá trị NULL gom vào khóa 'UNKNOWN'.
    """
    rows = (await db.execute(select(column, func.count()).group_by(column))).all()
    out: dict[str, int] = {}
    for val, cnt in rows:
        if val is None:
            key = "UNKNOWN"
        elif hasattr(val, "value"):
            key = val.value
        else:
            key = str(val)
        out[key] = out.get(key, 0) + int(cnt)
    return out
