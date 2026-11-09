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

    # Notify Admin khi có đề xuất mới chờ duyệt
    if not has_stock:
        try:
            from app.modules.system.models import Notification, NotificationType
            from app.modules.auth.models import User as _User, ROLE_NAME_TO_ID as _ROLE_MAP
            from app.shared.roles import UserRole as _Role
            _admin_role = _ROLE_MAP.get(_Role.ADMIN)
            if _admin_role:
                _admin_ids = (await db.execute(
                    select(_User.id).where(_User.role_id == _admin_role)
                )).scalars().all()
                for _uid in _admin_ids:
                    db.add(Notification(
                        user_id=_uid,
                        title=f"🔧 Đề xuất linh kiện mới — Hub #{hub_id}",
                        message=(
                            f"Kỹ thuật viên đề xuất sử dụng {body.quantity_needed}x {cat.name} "
                            f"{'cho drone ' + body.drone_id if body.drone_id else ''}. "
                            f"Hub #{hub_id} không đủ hàng — cần Admin duyệt."
                        ),
                        notifications_type=NotificationType.MAINTENANCE,
                        reference_id=f"PARTS_REQUEST_{req.id}",
                        created_at=now,
                    ))
                await db.commit()
        except Exception as _e:
            print(f"[NOTIF] Parts request notify error: {_e}")

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


# ══════════════════════════════════════════════════════════════════
# HUB-LEVEL SUPPLY MANAGEMENT (Technician tại Hub mình)
# ══════════════════════════════════════════════════════════════════

class AdjustBody(BaseModel):
    part_category_id: int
    quantity_delta: int          # dương = tìm thêm, âm = mất / sai số
    note: Optional[str] = None


class DiscardBody(BaseModel):
    part_category_id: int
    quantity: int
    note: Optional[str] = None   # lý do: vỡ, han gỉ, hết hạn...


class ThresholdBody(BaseModel):
    part_category_id: int
    min_stock_threshold: int


@router.get("/hub-detail", summary="Chi tiết tồn kho spare part tại Hub của Technician")
async def get_my_hub_detail(
    low_stock_only: bool = Query(False),
    category_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Xem toàn bộ spare part tại Hub của mình — số lượng, ngưỡng cảnh báo,
    giá tham chiếu, trạng thái low_stock.
    """
    from app.modules.system.models import Hub
    hub_id = _get_tech_hub(user)
    hub = await db.get(Hub, hub_id)

    q = (
        select(HubSupply, PartCategory)
        .join(PartCategory, HubSupply.part_category_id == PartCategory.id)
        .where(HubSupply.hub_id == hub_id)
        .order_by(PartCategory.category_type, PartCategory.name)
    )
    if low_stock_only:
        q = q.where(HubSupply.quantity <= HubSupply.min_stock_threshold)
    if category_type:
        q = q.where(PartCategory.category_type == category_type)

    rows = (await db.execute(q)).all()
    total_value = sum((cat.unit_cost or 0) * inv.quantity for inv, cat in rows)

    return {
        "hub_id": hub_id,
        "hub_name": hub.name if hub else f"Hub #{hub_id}",
        "total_sku": len(rows),
        "low_stock_count": sum(1 for inv, _ in rows if inv.quantity <= inv.min_stock_threshold),
        "estimated_stock_value": round(total_value, 0),
        "supplies": [
            {
                "id": inv.id,
                "part_category_id": cat.id,
                "part_name": cat.name,
                "category_type": cat.category_type.value if hasattr(cat.category_type, "value") else str(cat.category_type),
                "compatible_drone_models": cat.compatible_drone_models,
                "quantity": inv.quantity,
                "min_stock_threshold": inv.min_stock_threshold,
                "is_low_stock": inv.quantity <= inv.min_stock_threshold,
                "unit_cost": cat.unit_cost,
                "updated_at": inv.updated_at.isoformat() if inv.updated_at else None,
            }
            for inv, cat in rows
        ],
    }


@router.post("/adjust", summary="Điều chỉnh số lượng sau kiểm kê thực tế")
async def adjust_supply(
    body: AdjustBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Technician điều chỉnh số lượng thực tế tại Hub của mình sau khi kiểm kê.
    Sinh SupplyTransaction với reason=Adjust để ghi log.
    """
    hub_id = _get_tech_hub(user)
    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Danh mục linh kiện không tồn tại.")

    now = datetime.now(timezone.utc)
    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if not inv:
        if body.quantity_delta < 0:
            raise HTTPException(status_code=400, detail="Hub chưa có mục linh kiện này.")
        inv = HubSupply(
            hub_id=hub_id, part_category_id=body.part_category_id,
            quantity=body.quantity_delta, min_stock_threshold=2, updated_at=now,
        )
        db.add(inv)
    else:
        new_qty = inv.quantity + body.quantity_delta
        if new_qty < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Tồn kho {inv.quantity}, điều chỉnh {body.quantity_delta} sẽ âm.",
            )
        inv.quantity = new_qty
        inv.updated_at = now

    db.add(SupplyTransaction(
        hub_id=hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity_delta, reason=SupplyTxnReason.ADJUST,
        performed_by=user.id,
        note=f"[Tech] Kiểm kê điều chỉnh {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name}. "
             + (body.note or ""),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Đã điều chỉnh {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name}.",
        "new_quantity": inv.quantity,
        "is_low_stock": inv.quantity <= inv.min_stock_threshold,
    }


@router.post("/discard", summary="Ghi nhận linh kiện hỏng / hết hạn tại Hub")
async def discard_supply(
    body: DiscardBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Technician ghi nhận linh kiện không còn sử dụng được (vỡ, hỏng, hết hạn)
    → trừ tồn kho, log lý do.
    """
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Số lượng hủy tối thiểu là 1.")
    hub_id = _get_tech_hub(user)
    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Danh mục linh kiện không tồn tại.")

    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if not inv or inv.quantity < body.quantity:
        avail = inv.quantity if inv else 0
        raise HTTPException(
            status_code=400,
            detail=f"Tồn kho chỉ còn {avail} đơn vị, không đủ để hủy {body.quantity}.",
        )

    now = datetime.now(timezone.utc)
    inv.quantity -= body.quantity
    inv.updated_at = now

    db.add(SupplyTransaction(
        hub_id=hub_id, part_category_id=body.part_category_id,
        quantity_delta=-body.quantity, reason=SupplyTxnReason.DISCARD,
        performed_by=user.id,
        note=f"[Tech] Hủy {body.quantity}x {cat.name}. " + (body.note or "Linh kiện hỏng."),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Đã ghi nhận hủy {body.quantity}x {cat.name}.",
        "remaining_quantity": inv.quantity,
        "is_low_stock": inv.quantity <= inv.min_stock_threshold,
    }


@router.patch("/threshold", summary="Đặt ngưỡng cảnh báo tồn kho tại Hub")
async def set_threshold(
    body: ThresholdBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Technician tự thiết lập ngưỡng cảnh báo cho từng loại linh kiện tại Hub mình.
    Khi quantity <= min_stock_threshold → hệ thống đánh dấu low_stock.
    """
    if body.min_stock_threshold < 0:
        raise HTTPException(status_code=400, detail="Ngưỡng phải >= 0.")
    hub_id = _get_tech_hub(user)
    now = datetime.now(timezone.utc)

    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if not inv:
        inv = HubSupply(
            hub_id=hub_id, part_category_id=body.part_category_id,
            quantity=0, min_stock_threshold=body.min_stock_threshold, updated_at=now,
        )
        db.add(inv)
    else:
        inv.min_stock_threshold = body.min_stock_threshold
        inv.updated_at = now

    await db.commit()
    return {
        "message": f"Đã đặt ngưỡng cảnh báo = {body.min_stock_threshold}.",
        "part_category_id": body.part_category_id,
        "min_stock_threshold": body.min_stock_threshold,
        "current_quantity": inv.quantity,
        "is_low_stock": inv.quantity <= body.min_stock_threshold,
    }


@router.get("/transactions", summary="Lịch sử biến động vật tư tại Hub của Technician")
async def get_my_hub_transactions(
    part_category_id: Optional[int] = Query(None),
    reason: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """Xem log tất cả biến động vật tư tại Hub của mình (nhập, xuất, hủy, điều chỉnh)."""
    hub_id = _get_tech_hub(user)
    q = select(SupplyTransaction).where(
        SupplyTransaction.hub_id == hub_id
    ).order_by(SupplyTransaction.created_at.desc()).limit(limit)

    if part_category_id:
        q = q.where(SupplyTransaction.part_category_id == part_category_id)
    if reason:
        q = q.where(SupplyTransaction.reason == reason)

    rows = (await db.execute(q)).scalars().all()
    cat_ids = list({r.part_category_id for r in rows})
    cats = {c.id: c.name for c in (await db.execute(
        select(PartCategory).where(PartCategory.id.in_(cat_ids))
    )).scalars().all()} if cat_ids else {}

    return [
        {
            "id": r.id,
            "part_name": cats.get(r.part_category_id, f"#part_{r.part_category_id}"),
            "quantity_delta": r.quantity_delta,
            "reason": r.reason.value if hasattr(r.reason, "value") else str(r.reason),
            "note": r.note,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
