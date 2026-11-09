"""
[Technician] Supplies — Xem và đề xuất linh kiện tại Hub của mình

Endpoints:
  GET  /technician/supplies                       — vật tư tại Hub mình
  GET  /technician/supplies/categories            — danh mục linh kiện
  POST /technician/supplies/parts-requests        — tạo đề xuất thay linh kiện
  GET  /technician/supplies/parts-requests        — danh sách đề xuất của mình
  GET  /technician/supplies/parts-requests/{id}   — chi tiết đề xuất
  PATCH /technician/supplies/parts-requests/{id}/fulfill — xác nhận đã thay xong
"""

from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import CurrentUser, RoleChecker
from app.shared.roles import UserRole
from app.modules.supplies.models import (
    PartCategory, HubSupply, SupplyTransaction, PartsRequest,
    PartsRequestStatus, SupplyTxnReason,
)

router = APIRouter(prefix="/technician/supplies", tags=["Technician - Supplies"])
allow_tech = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])


# ── Schemas ────────────────────────────────────────────────────────

class PartCategoryOut(BaseModel):
    id: int
    name: str
    category_type: str
    compatible_drone_models: Optional[List[str]] = None
    unit_cost: Optional[float] = None
    description: Optional[str] = None
    class Config: from_attributes = True


class HubSupplyOut(BaseModel):
    id: int
    hub_id: int
    part_category_id: int
    part_name: str
    category_type: str
    quantity: int
    min_stock_threshold: int
    is_low_stock: bool
    unit_cost: Optional[float] = None
    compatible_drone_models: Optional[List[str]] = None


class PartsRequestCreate(BaseModel):
    drone_id: Optional[str] = None
    part_category_id: int
    quantity_needed: int = 1
    description: str
    photo_urls: Optional[List[str]] = None


class PartsRequestOut(BaseModel):
    id: int
    drone_id: Optional[str] = None
    hub_id: int
    part_category_id: int
    part_name: Optional[str] = None
    quantity_needed: int
    description: str
    photo_urls: Optional[List[str]] = None
    status: str
    admin_note: Optional[str] = None
    actual_cost: Optional[float] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    fulfilled_at: Optional[datetime] = None
    class Config: from_attributes = True


def _get_tech_hub(user: CurrentUser) -> int:
    if not user.hub_id:
        raise HTTPException(status_code=400, detail="Technician chưa được gán Hub. Liên hệ Admin.")
    return user.hub_id


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/categories", response_model=List[PartCategoryOut],
            summary="Danh mục linh kiện / vật tư có thể đề xuất")
async def list_categories(
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    rows = (await db.execute(
        select(PartCategory).where(PartCategory.is_active == True).order_by(PartCategory.name)
    )).scalars().all()
    return rows


@router.get("", response_model=List[HubSupplyOut],
            summary="Vật tư / linh kiện hiện có tại Hub của Technician")
async def get_hub_supplies(
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    hub_id = _get_tech_hub(user)
    rows = (await db.execute(
        select(HubSupply, PartCategory)
        .join(PartCategory, HubSupply.part_category_id == PartCategory.id)
        .where(HubSupply.hub_id == hub_id)
        .order_by(PartCategory.category_type, PartCategory.name)
    )).all()
    return [
        HubSupplyOut(
            id=inv.id, hub_id=inv.hub_id, part_category_id=inv.part_category_id,
            part_name=cat.name,
            category_type=cat.category_type.value if hasattr(cat.category_type, "value") else str(cat.category_type),
            quantity=inv.quantity, min_stock_threshold=inv.min_stock_threshold,
            is_low_stock=inv.quantity <= inv.min_stock_threshold,
            unit_cost=cat.unit_cost, compatible_drone_models=cat.compatible_drone_models,
        )
        for inv, cat in rows
    ]


@router.post("/parts-requests", response_model=PartsRequestOut,
             summary="Tạo đề xuất sử dụng linh kiện để sửa chữa")
async def create_parts_request(
    body: PartsRequestCreate,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Technician tạo đề xuất.
    - Hub đủ hàng → AUTO_APPROVED, trừ ngay.
    - Không đủ → PENDING, chờ Admin duyệt / điều phối.
    """
    hub_id = _get_tech_hub(user)
    now = datetime.now(timezone.utc)

    cat = await db.get(PartCategory, body.part_category_id)
    if not cat or not cat.is_active:
        raise HTTPException(status_code=404, detail="Loại linh kiện không tồn tại hoặc đã ngừng.")
    if body.quantity_needed < 1:
        raise HTTPException(status_code=400, detail="Số lượng tối thiểu là 1.")

    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    has_stock = inv is not None and inv.quantity >= body.quantity_needed
    status = PartsRequestStatus.AUTO_APPROVED if has_stock else PartsRequestStatus.PENDING

    req = PartsRequest(
        requested_by=user.id, hub_id=hub_id, drone_id=body.drone_id,
        part_category_id=body.part_category_id, quantity_needed=body.quantity_needed,
        description=body.description, photo_urls=body.photo_urls,
        status=status, created_at=now,
    )
    if has_stock:
        req.reviewed_at = now
        req.admin_note = "Tự động duyệt — Hub đủ vật tư."

    db.add(req)
    await db.flush()

    if has_stock:
        inv.quantity -= body.quantity_needed
        inv.updated_at = now
        db.add(SupplyTransaction(
            hub_id=hub_id, part_category_id=body.part_category_id,
            quantity_delta=-body.quantity_needed, reason=SupplyTxnReason.REPAIR_USE,
            performed_by=user.id, reference_id=str(req.id),
            note=f"Auto-approved: sử dụng cho drone {body.drone_id or 'N/A'}",
            created_at=now,
        ))

    await db.commit()
    await db.refresh(req)
    out = PartsRequestOut.model_validate(req)
    out.part_name = cat.name
    return out


@router.get("/parts-requests", response_model=List[PartsRequestOut],
            summary="Đề xuất linh kiện của Technician hiện tại")
async def list_my_requests(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    hub_id = _get_tech_hub(user)
    q = select(PartsRequest).where(and_(
        PartsRequest.requested_by == user.id,
        PartsRequest.hub_id == hub_id,
    )).order_by(PartsRequest.created_at.desc())
    if status:
        q = q.where(PartsRequest.status == status)

    rows = (await db.execute(q)).scalars().all()
    cat_ids = list({r.part_category_id for r in rows})
    cats = {c.id: c.name for c in (await db.execute(
        select(PartCategory).where(PartCategory.id.in_(cat_ids))
    )).scalars().all()} if cat_ids else {}

    result = []
    for r in rows:
        out = PartsRequestOut.model_validate(r)
        out.part_name = cats.get(r.part_category_id)
        result.append(out)
    return result


@router.get("/parts-requests/{request_id}", response_model=PartsRequestOut,
            summary="Chi tiết đề xuất linh kiện")
async def get_request_detail(
    request_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
    if req.requested_by != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Không có quyền xem đề xuất này.")
    cat = await db.get(PartCategory, req.part_category_id)
    out = PartsRequestOut.model_validate(req)
    out.part_name = cat.name if cat else None
    return out


@router.patch("/parts-requests/{request_id}/fulfill",
              summary="Xác nhận đã thay linh kiện xong")
async def mark_fulfilled(
    request_id: int,
    actual_cost: Optional[float] = Query(None, description="Chi phí thực tế (VNĐ)"),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
    if req.requested_by != user.id:
        raise HTTPException(status_code=403, detail="Chỉ người tạo mới được xác nhận.")
    if req.status not in (PartsRequestStatus.APPROVED, PartsRequestStatus.AUTO_APPROVED):
        raise HTTPException(status_code=400,
            detail=f"Trạng thái '{req.status.value}' không thể xác nhận hoàn thành.")
    req.status = PartsRequestStatus.FULFILLED
    req.fulfilled_at = datetime.now(timezone.utc)
    if actual_cost is not None:
        req.actual_cost = actual_cost
    await db.commit()
    return {"message": "Xác nhận thay linh kiện thành công.", "request_id": request_id,
            "status": PartsRequestStatus.FULFILLED.value}
