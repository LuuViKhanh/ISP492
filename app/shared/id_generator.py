from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from app.core.timezone import VN_TIMEZONE

async def generate_sequential_id(db: AsyncSession, model_class, prefix: str) -> str:
    """
    Generates a sequential ID formatted as PREFIX-YYYYMMDD-XXXX or PREFIX-XXXX
    - Order: ORD-YYYYMMDD-XXXX (theo múi giờ Việt Nam GMT+7)
    - Mission: MSN-YYYYMMDD-XXXX (theo múi giờ Việt Nam GMT+7)
    - Battery: BAT-XXXX
    - Drone: DRN-XXXX
    """
    is_daily = prefix in ["ORD", "MSN"]
    
    if is_daily:
        today_str = datetime.now(VN_TIMEZONE).strftime("%Y%m%d")
        full_prefix = f"{prefix}-{today_str}-"
    else:
        full_prefix = f"{prefix}-"
        
    query = select(model_class.id).where(model_class.id.like(f"{full_prefix}%")).order_by(model_class.id.desc()).limit(1)
    result = await db.execute(query)
    last_id = result.scalar()
    
    if last_id:
        try:
            last_num = int(last_id.split("-")[-1])
            next_num = last_num + 1
        except (ValueError, IndexError):
            next_num = 1
    else:
        next_num = 1
        
    return f"{full_prefix}{next_num:04d}"

