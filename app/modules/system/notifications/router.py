from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.database.db import get_async_db
from app.shared.dependencies import get_current_user, CurrentUser
from app.modules.system.models import Notification, NotificationType

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationResponse(BaseModel):
    id: int
    user_id: str
    title: str
    message: str
    notifications_type: NotificationType
    reference_id: Optional[str]
    is_read: bool
    created_at: datetime
    drone_id: Optional[int]
    work_order_id: Optional[int]
    mission_id: Optional[str]

    class Config:
        from_attributes = True


@router.get("/", response_model=list[NotificationResponse])
async def get_notifications(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
    )
    return result.scalars().all()


@router.get("/unread-count")
async def get_unread_count(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == user.id,
            Notification.is_read == False
        )
    )
    return {"unread_count": len(result.scalars().all())}


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
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


@router.patch("/read-all")
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
    return {"message": "All notifications marked as read"}
