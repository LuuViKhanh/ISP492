import re
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from app.core.timezone import VN_TIMEZONE


_DRONE_MODEL_CODE_MAP = {
    "dji matrice 100": "M100",
    "aurelia x6 pro": "X6P",
    "dji flycart 15 heavy": "FC15",
}

_BATTERY_MODEL_CODE_MAP = {
    "dji tb48d": "TB48",
    "tb48d": "TB48",
    "dji-bat-130w": "TB48",
    "aurelia ultrapower 12s": "UP12",
    "aurelia ultrapower": "UP12",
    "aur-bat-444w": "UP12",
    "dji db2000": "DB20",
    "db2000": "DB20",
    "dji-bat-980w": "DB20",
}


def get_drone_model_code(model: Optional[str], payload_capacity_kg: Optional[float] = None) -> str:
    """Map Drone model name to short URL-safe code (M100, X6P, FC15)."""
    norm = " ".join((model or "").strip().lower().split())
    if norm in _DRONE_MODEL_CODE_MAP:
        return _DRONE_MODEL_CODE_MAP[norm]
    if "matrice" in norm:
        return "M100"
    if "aurelia" in norm:
        return "X6P"
    if "flycart" in norm:
        return "FC15"
    if payload_capacity_kg is not None:
        cap = float(payload_capacity_kg)
        if cap <= 2.0:
            return "M100"
        if cap <= 6.0:
            return "X6P"
        return "FC15"
    slug = re.sub(r"[^A-Za-z0-9]+", "", model or "").upper()[:6]
    return slug or "GEN"


def get_battery_model_code(battery_model: Optional[str]) -> str:
    """Map Battery model name to short URL-safe code (TB48, UP12, DB20)."""
    norm = " ".join((battery_model or "").strip().lower().split())
    if norm in _BATTERY_MODEL_CODE_MAP:
        return _BATTERY_MODEL_CODE_MAP[norm]
    if "tb48" in norm or "130w" in norm:
        return "TB48"
    if "ultrapower" in norm or "up12" in norm or "444w" in norm:
        return "UP12"
    if "db2000" in norm or "980w" in norm:
        return "DB20"
    slug = re.sub(r"[^A-Za-z0-9]+", "", battery_model or "").upper()[:6]
    return slug or "GEN"


async def generate_sequential_id(
    db: AsyncSession,
    model_class,
    prefix: str,
    model_name: Optional[str] = None,
    payload_capacity_kg: Optional[float] = None,
) -> str:
    """
    Generates a sequential ID formatted as:
    - Order: ORD-YYYYMMDD-XXXX (theo múi giờ Việt Nam GMT+7)
    - Mission: MSN-YYYYMMDD-XXXX (theo múi giờ Việt Nam GMT+7)
    - Drone: {ModelCode}-DRNXXXX (vd: M100-DRN0001) hoặc DRN-XXXX nếu không truyền model_name
    - Battery: {BatteryCode}-BATXXXX (vd: TB48-BAT0001) hoặc BAT-XXXX nếu không truyền model_name
    """
    is_daily = prefix in ["ORD", "MSN"]

    if is_daily:
        today_str = datetime.now(VN_TIMEZONE).strftime("%Y%m%d")
        full_prefix = f"{prefix}-{today_str}-"
        query = select(model_class.id).where(model_class.id.like(f"{full_prefix}%")).order_by(model_class.id.desc()).limit(1)
        result = await db.execute(query)
        last_id = result.scalar()
        if last_id:
            try:
                next_num = int(last_id.split("-")[-1]) + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1
        return f"{full_prefix}{next_num:04d}"

    # DRN / BAT: tìm max_num toàn cục qua mọi format ({CODE}-DRNxxxx hoặc DRN-xxxx)
    query = select(model_class.id).where(model_class.id.like(f"%{prefix}%"))
    result = await db.execute(query)
    pattern = re.compile(rf"{re.escape(prefix)}-?(\d+)$")
    max_num = 0
    for row_id in result.scalars().all():
        m = pattern.search(str(row_id or ""))
        if m:
            max_num = max(max_num, int(m.group(1)))

    next_num = max_num + 1
    if prefix == "DRN" and model_name:
        code = get_drone_model_code(model_name, payload_capacity_kg)
        return f"{code}-DRN{next_num:04d}"
    if prefix == "BAT" and model_name:
        code = get_battery_model_code(model_name)
        return f"{code}-BAT{next_num:04d}"
    return f"{prefix}-{next_num:04d}"

