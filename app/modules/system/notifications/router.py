"""
Notifications API — Chuông thông báo cho từng role

GET  /notifications            — danh sách thông báo (có pagination + filter)
GET  /notifications/unread-count — số thông báo chưa đọc
PATCH /notifications/{id}/read  — đánh dấu đã đọc
PATCH /notifications/read-all   — đọc tất cả
DELETE /notifications/{id}      — xóa 1 thông báo
DELETE /notifications/clear-read — xóa tất cả đã đọc
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_, func
from pydantic import BaseModel
from typing import Optional, Union, List
from datetime import datetime

from app.database.db import get_async_db
from app.shared.dependencies import get_current_user, CurrentUser
from app.modules.system.models import Notification, NotificationType

router = APIRouter(prefix="/notifications", tags=["System"])


class NotificationResponse(BaseModel):
    id: int
    user_id: str
    title: str
    message: str
    notifications_type: NotificationType
    reference_id: Optional[str] = None
    is_read: bool
    created_at: datetime
    drone_id: Optional[Union[str, int]] = None
    work_order_id: Optional[int] = None
    mission_id: Optional[str] = None

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    total: int
    unread_count: int
    page: int
    page_size: int
    items: List[NotificationResponse]


# ── GET list (với pagination + filter) ────────────────────────────

@router.get("", response_model=NotificationListResponse,
            summary="Danh sách thông báo (chuông 🔔) của người dùng")
async def get_notifications(
    page: int = Query(1, ge=1, description="Trang hiện tại"),
    page_size: int = Query(20, ge=1, le=100, description="Số thông báo mỗi trang"),
    is_read: Optional[bool] = Query(None, description="true = đã đọc, false = chưa đọc"),
    notification_type: Optional[NotificationType] = Query(None, description="Lọc theo loại"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy thông báo của người dùng hiện tại.
    Hỗ trợ phân trang và lọc theo trạng thái đọc / loại thông báo.
    """
    base_q = select(Notification).where(Notification.user_id == user.id)
    if is_read is not None:
        base_q = base_q.where(Notification.is_read == is_read)
    if notification_type is not None:
        base_q = base_q.where(Notification.notifications_type == notification_type)

    total = (await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )).scalar() or 0

    unread = (await db.execute(
        select(func.count()).select_from(
            select(Notification)
            .where(Notification.user_id == user.id, Notification.is_read == False)
            .subquery()
        )
    )).scalar() or 0

    items = (await db.execute(
        base_q.order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    return NotificationListResponse(
        total=total,
        unread_count=unread,
        page=page,
        page_size=page_size,
        items=items,
    )


# ── GET unread count ────────────────────────────────────────────────

@router.get("/unread-count", summary="Số thông báo chưa đọc")
async def get_unread_count(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = (await db.execute(
        select(func.count()).select_from(
            select(Notification)
            .where(Notification.user_id == user.id, Notification.is_read == False)
            .subquery()
        )
    )).scalar() or 0
    return {"unread_count": int(result)}


# ── PATCH mark one read ─────────────────────────────────────────────

@router.patch("/{notification_id}/read", response_model=NotificationResponse,
              summary="Đánh dấu đã đọc một thông báo")
async def mark_as_read(
    notification_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notif.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your notification")
    notif.is_read = True
    await db.commit()
    await db.refresh(notif)
    return notif


# ── PATCH mark all read ─────────────────────────────────────────────

@router.patch("/read-all", summary="Đánh dấu tất cả thông báo là đã đọc")
async def mark_all_as_read(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "Tất cả thông báo đã được đánh dấu là đã đọc."}


# ── DELETE one ──────────────────────────────────────────────────────

@router.delete("/{notification_id}", status_code=204,
               summary="Xóa một thông báo")
async def delete_notification(
    notification_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notif.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your notification")
    await db.delete(notif)
    await db.commit()


# ── DELETE all read ─────────────────────────────────────────────────

@router.delete("/clear-read", summary="Xóa tất cả thông báo đã đọc")
async def clear_read_notifications(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        delete(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == True)
    )
    await db.commit()
    return {"message": f"Đã xóa {result.rowcount} thông báo đã đọc."}
