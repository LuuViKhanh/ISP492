"""
[Admin] Orders — Ratings Management
Prefix: /admin/orders
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import CurrentUser, RoleChecker
from app.shared.roles import UserRole
from app.modules.missions.models import DeliveryRating, Order

router = APIRouter(prefix="/admin/orders", tags=["Admin - Orders"])
allow_admin = RoleChecker([UserRole.ADMIN])


# ── Schemas ──────────────────────────────────────────────────────────

class RatingItem(BaseModel):
    id: int
    order_id: str
    customer_id: str
    rating: int
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class RatingsSummaryResponse(BaseModel):
    total_ratings: int
    avg_rating: float
    distribution: dict   # {"1": 3, "2": 5, "3": 10, "4": 25, "5": 57}
    ratings: List[RatingItem]


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get(
    "/ratings",
    response_model=RatingsSummaryResponse,
    summary="Tổng hợp đánh giá giao hàng của khách hàng",
)
async def get_all_ratings(
    rating: Optional[int] = Query(None, ge=1, le=5, description="Filter theo số sao"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin API] Xem tất cả đánh giá giao hàng.**

    - Thống kê tổng số rating, điểm trung bình, phân phối 1-5 sao.
    - Filter theo số sao cụ thể.
    - Phân trang với `limit` / `offset`.
    """
    # Thống kê tổng hợp
    stats = (await db.execute(
        select(
            func.count(DeliveryRating.id).label("total"),
            func.avg(DeliveryRating.rating).label("avg_rating"),
        )
    )).one()

    # Phân phối 1-5 sao
    dist_rows = (await db.execute(
        select(DeliveryRating.rating, func.count(DeliveryRating.id))
        .group_by(DeliveryRating.rating)
        .order_by(DeliveryRating.rating)
    )).all()
    distribution = {str(r): c for r, c in dist_rows}
    # Đảm bảo đủ 1-5
    for star in range(1, 6):
        distribution.setdefault(str(star), 0)

    # Danh sách ratings (có filter + pagination)
    q = select(DeliveryRating).order_by(DeliveryRating.created_at.desc())
    if rating:
        q = q.where(DeliveryRating.rating == rating)
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()

    return RatingsSummaryResponse(
        total_ratings=stats.total or 0,
        avg_rating=round(float(stats.avg_rating), 2) if stats.avg_rating else 0.0,
        distribution=distribution,
        ratings=rows,
    )
