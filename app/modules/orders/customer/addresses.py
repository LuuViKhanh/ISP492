"""
[Customer] Address Book — Danh bạ địa chỉ giao hàng
"""
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import CurrentUser
from app.shared.dependencies import get_current_user
from app.modules.missions.models import SavedAddress

router = APIRouter(prefix="/customer/addresses", tags=["Customer - Address Book"])

# ── Schemas ───────────────────────────────────────────────────────────────────

class SavedAddressCreate(BaseModel):
    label: str = Field(..., max_length=100, description="Nhãn địa chỉ, e.g. 'Home', 'Office'")
    recipient_name: str = Field(..., max_length=100)
    recipient_phone: str = Field(..., max_length=20)
    recipient_email: Optional[str] = Field(default=None, max_length=255)
    hub_id: Optional[int] = Field(default=None, description="Hub đích hay dùng")
    is_default: bool = False


class SavedAddressOut(BaseModel):
    id: int
    label: str
    recipient_name: str
    recipient_phone: str
    recipient_email: Optional[str]
    hub_id: Optional[int]
    is_default: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[SavedAddressOut], summary="Danh sách địa chỉ đã lưu")
async def list_addresses(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """**[Customer] Lấy toàn bộ địa chỉ đã lưu, sắp xếp default trước.**"""
    rows = (await db.execute(
        select(SavedAddress)
        .where(SavedAddress.customer_id == str(current_user.id))
        .order_by(SavedAddress.is_default.desc(), SavedAddress.id.desc())
    )).scalars().all()
    return rows


@router.post("", response_model=SavedAddressOut, status_code=201, summary="Thêm địa chỉ mới")
async def create_address(
    body: SavedAddressCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer] Thêm địa chỉ người nhận vào danh bạ.**
    Nếu `is_default=True`, tự động bỏ default của các địa chỉ khác.
    """
    # Max 20 địa chỉ per customer
    count = (await db.execute(
        select(SavedAddress).where(SavedAddress.customer_id == str(current_user.id))
    )).scalars().all()
    if len(count) >= 20:
        raise HTTPException(status_code=400, detail="Maximum 20 saved addresses allowed.")

    if body.is_default:
        # Unset current defaults
        for addr in count:
            if addr.is_default:
                addr.is_default = False

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    addr = SavedAddress(
        customer_id=str(current_user.id),
        label=body.label.strip(),
        recipient_name=body.recipient_name.strip(),
        recipient_phone=body.recipient_phone.strip(),
        recipient_email=body.recipient_email,
        hub_id=body.hub_id,
        is_default=body.is_default,
        created_at=now,
    )
    db.add(addr)
    await db.commit()
    await db.refresh(addr)
    return addr


@router.patch("/{address_id}", response_model=SavedAddressOut, summary="Cập nhật địa chỉ")
async def update_address(
    address_id: int,
    body: SavedAddressCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """**[Customer] Cập nhật thông tin địa chỉ đã lưu.**"""
    addr = await db.get(SavedAddress, address_id)
    if not addr or addr.customer_id != str(current_user.id):
        raise HTTPException(status_code=404, detail="Address not found.")

    if body.is_default and not addr.is_default:
        # Unset others
        others = (await db.execute(
            select(SavedAddress).where(
                SavedAddress.customer_id == str(current_user.id),
                SavedAddress.is_default == True,
                SavedAddress.id != address_id,
            )
        )).scalars().all()
        for o in others:
            o.is_default = False

    addr.label          = body.label.strip()
    addr.recipient_name  = body.recipient_name.strip()
    addr.recipient_phone = body.recipient_phone.strip()
    addr.recipient_email = body.recipient_email
    addr.hub_id          = body.hub_id
    addr.is_default      = body.is_default

    await db.commit()
    await db.refresh(addr)
    return addr


@router.delete("/{address_id}", status_code=204, summary="Xóa địa chỉ")
async def delete_address(
    address_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """**[Customer] Xóa địa chỉ khỏi danh bạ.**"""
    addr = await db.get(SavedAddress, address_id)
    if not addr or addr.customer_id != str(current_user.id):
        raise HTTPException(status_code=404, detail="Address not found.")
    await db.delete(addr)
    await db.commit()
