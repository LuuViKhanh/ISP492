from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.fleet.models import Drone

def get_drone_prefix(model: Optional[str], payload_capacity_kg: Optional[float] = None) -> str:
    """
    Xác định tiền tố tên máy bay theo quy tắc chuẩn hóa:
    - Light (<= 1.0kg / DJI Matrice 100) -> Swift
    - Medium (<= 5.0kg / Aurelia X6 Pro) -> Falcon
    - Heavy (> 5.0kg / DJI FlyCart 15 Heavy) -> Eagle
    """
    m = (model or "").lower()
    cap = float(payload_capacity_kg or 0.0)
    if "matrice" in m or (cap > 0 and cap <= 2.0):
        return "Swift"
    elif "aurelia" in m or (cap > 2.0 and cap <= 6.0):
        return "Falcon"
    else:
        return "Eagle"

async def generate_drone_name(
    db: AsyncSession,
    model: Optional[str],
    payload_capacity_kg: Optional[float] = None
) -> str:
    """
    Tự động sinh tên tiếp theo cho Drone mới dựa trên phân loại Swift / Falcon / Eagle.
    Ví dụ: nếu đã có Swift 01 .. Swift 40, sẽ tự động sinh 'Swift 41'.
    """
    prefix = get_drone_prefix(model, payload_capacity_kg)
    result = await db.execute(
        select(Drone.name).where(Drone.name.like(f"{prefix} %"))
    )
    names = result.scalars().all()
    max_num = 0
    for n in names:
        parts = n.strip().split()
        if len(parts) >= 2 and parts[1].isdigit():
            try:
                max_num = max(max_num, int(parts[1]))
            except ValueError:
                pass

    next_num = max_num + 1
    return f"{prefix} {next_num:02d}"
