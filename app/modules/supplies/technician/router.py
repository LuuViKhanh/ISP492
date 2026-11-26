"""
[Technician] Supplies â€” Xem vÃ  quáº£n lÃ½ linh kiá»‡n táº¡i Hub cá»§a mÃ¬nh

Endpoints:
  GET  /technician/supplies                       â€” váº­t tÆ° táº¡i Hub mÃ¬nh
  GET  /technician/supplies/categories            â€” danh má»¥c linh kiá»‡n
  POST /technician/supplies/parts-requests        â€” táº¡o Ä‘á» xuáº¥t thay linh kiá»‡n
  GET  /technician/supplies/parts-requests        â€” danh sÃ¡ch Ä‘á» xuáº¥t cá»§a mÃ¬nh
  GET  /technician/supplies/parts-requests/{id}   â€” chi tiáº¿t Ä‘á» xuáº¥t
  PATCH /technician/supplies/parts-requests/{id}/fulfill â€” xÃ¡c nháº­n Ä‘Ã£ thay xong
  GET  /technician/supplies/hub-detail            â€” chi tiáº¿t tá»“n kho táº¡i Hub
  POST /technician/supplies/adjust                â€” Ä‘iá»u chá»‰nh sá»‘ lÆ°á»£ng kiá»ƒm kÃª
  POST /technician/supplies/discard               â€” ghi nháº­n linh kiá»‡n há»ng
  PATCH /technician/supplies/threshold            â€” Ä‘áº·t ngÆ°á»¡ng cáº£nh bÃ¡o
  GET  /technician/supplies/transactions          â€” lá»‹ch sá»­ biáº¿n Ä‘á»™ng váº­t tÆ°
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
from app.modules.supplies.audit import audit_supply_action
from app.modules.supplies.models import (
    PartCategory, HubSupply, SupplyTransaction, PartsRequest,
    PartsRequestStatus, SupplyTxnReason,
)

router = APIRouter(prefix="/technician/supplies", tags=["Technician - Supplies"])
allow_tech = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])


# â”€â”€ Schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        raise HTTPException(status_code=400, detail="Technician is not assigned to any hub. Please contact Admin.")
    return user.hub_id


# â”€â”€ Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/categories", response_model=List[PartCategoryOut],
            summary="Danh má»¥c linh kiá»‡n / váº­t tÆ° cÃ³ thá»ƒ Ä‘á» xuáº¥t")
async def list_categories(
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    rows = (await db.execute(
        select(PartCategory).where(PartCategory.is_active == True).order_by(PartCategory.name)
    )).scalars().all()
    return [
        PartCategoryOut(
            id=c.id, name=c.name,
            category_type=c.category_type.value if hasattr(c.category_type, "value") else str(c.category_type),
            unit_cost=c.unit_cost,
            compatible_drone_models=c.compatible_drone_models,
            description=c.description,
        ) for c in rows
    ]


@router.get("", response_model=List[HubSupplyOut],
            summary="Váº­t tÆ° / linh kiá»‡n hiá»‡n cÃ³ táº¡i Hub cá»§a Technician")
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
             summary="Táº¡o Ä‘á» xuáº¥t sá»­ dá»¥ng linh kiá»‡n Ä‘á»ƒ sá»­a chá»¯a")
async def create_parts_request(
    body: PartsRequestCreate,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Technician táº¡o Ä‘á» xuáº¥t.
    - Hub Ä‘á»§ hÃ ng â†’ AUTO_APPROVED, trá»« ngay.
    - KhÃ´ng Ä‘á»§ â†’ PENDING, chá» Admin duyá»‡t / Ä‘iá»u phá»‘i.
    """
    hub_id = _get_tech_hub(user)
    now = datetime.now(timezone.utc)

    cat = await db.get(PartCategory, body.part_category_id)
    if not cat or not cat.is_active:
        raise HTTPException(status_code=404, detail="Part category not found or inactive.")
    if body.quantity_needed < 1:
        raise HTTPException(status_code=400, detail="Minimum quantity is 1.")

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
        req.admin_note = "Auto-approved â€” sufficient supplies at hub."

    db.add(req)
    await db.flush()

    if has_stock:
        inv.quantity -= body.quantity_needed
        inv.updated_at = now
        db.add(SupplyTransaction(
            hub_id=hub_id, part_category_id=body.part_category_id,
            quantity_delta=-body.quantity_needed, reason=SupplyTxnReason.REPAIR_USE,
            performed_by=user.id, reference_id=str(req.id),
            note=f"Auto-approved: used for drone {body.drone_id or 'N/A'}",
            created_at=now,
        ))

    await db.flush()

    # Notify Admin khi cÃ³ Ä‘á» xuáº¥t má»›i chá» duyá»‡t (trong cÃ¹ng 1 transaction)
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
                        title=f"New Parts Request â€” Hub #{hub_id}",
                        message=(
                            f"Technician requested {body.quantity_needed}x {cat.name}"
                            f"{' for drone ' + body.drone_id if body.drone_id else ''}. "
                            f"Hub #{hub_id} has insufficient stock â€” Admin approval required."
                        ),
                        notifications_type=NotificationType.MAINTENANCE,
                        reference_id=f"PARTS_REQUEST_{req.id}",
                        created_at=now,
                    ))
        except Exception as _e:
            print(f"[NOTIF] Parts request notify error: {_e}")

    await db.commit()
    await audit_supply_action(
        db, user, "CREATE_PARTS_REQUEST", req.id,
        f"Requested {body.quantity_needed}x {cat.name} at Hub #{hub_id}",
        details={"request_id": req.id, "hub_id": hub_id, "part_category_id": body.part_category_id,
                 "quantity_needed": body.quantity_needed, "drone_id": body.drone_id},
    )
    out = PartsRequestOut.model_validate(req)
    out.part_name = cat.name
    return out


@router.get("/parts-requests", response_model=List[PartsRequestOut],
            summary="Äá» xuáº¥t linh kiá»‡n cá»§a Technician hiá»‡n táº¡i")
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
            summary="Chi tiáº¿t Ä‘á» xuáº¥t linh kiá»‡n")
async def get_request_detail(
    request_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Parts request not found.")
    if req.requested_by != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="You do not have permission to view this parts request.")
    cat = await db.get(PartCategory, req.part_category_id)
    out = PartsRequestOut.model_validate(req)
    out.part_name = cat.name if cat else None
    return out


@router.patch("/parts-requests/{request_id}/fulfill",
              summary="XÃ¡c nháº­n Ä‘Ã£ thay linh kiá»‡n xong")
async def mark_fulfilled(
    request_id: int,
    actual_cost: Optional[float] = Query(None, description="Chi phÃ­ thá»±c táº¿ (VNÄ)"),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Parts request not found.")
    if req.requested_by != user.id:
        raise HTTPException(status_code=403, detail="Only the request creator can confirm fulfillment.")
    if req.status not in (PartsRequestStatus.APPROVED, PartsRequestStatus.AUTO_APPROVED):
        raise HTTPException(status_code=400,
            detail=f"Status '{req.status.value}' cannot be confirmed as fulfilled.")
    req.status = PartsRequestStatus.FULFILLED
    req.fulfilled_at = datetime.now(timezone.utc)
    if actual_cost is not None:
        req.actual_cost = actual_cost
    await db.commit()
    await audit_supply_action(
        db, user, "FULFILL_PARTS_REQUEST", req.id,
        f"Confirmed fulfillment of parts request #{req.id}",
        details={"request_id": req.id, "actual_cost": actual_cost},
    )
    return {"message": "Part replacement confirmed successfully.", "request_id": request_id,
            "status": PartsRequestStatus.FULFILLED.value}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# HUB-LEVEL SUPPLY MANAGEMENT (Technician táº¡i Hub mÃ¬nh)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class AdjustBody(BaseModel):
    part_category_id: int
    quantity_delta: int          # dÆ°Æ¡ng = tÃ¬m thÃªm, Ã¢m = máº¥t / sai sá»‘
    note: Optional[str] = None


class DiscardBody(BaseModel):
    part_category_id: int
    quantity: int
    note: Optional[str] = None   # lÃ½ do: vá»¡, han gá»‰, háº¿t háº¡n...


class ThresholdBody(BaseModel):
    part_category_id: int
    min_stock_threshold: int


@router.get("/hub-detail", summary="Chi tiáº¿t tá»“n kho spare part táº¡i Hub cá»§a Technician")
async def get_my_hub_detail(
    low_stock_only: bool = Query(False),
    category_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """
    Xem toÃ n bá»™ spare part táº¡i Hub cá»§a mÃ¬nh â€” sá»‘ lÆ°á»£ng, ngÆ°á»¡ng cáº£nh bÃ¡o,
    giÃ¡ tham chiáº¿u, tráº¡ng thÃ¡i low_stock.
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
    Technician Ä‘iá»u chá»‰nh sá»‘ lÆ°á»£ng thá»±c táº¿ táº¡i Hub cá»§a mÃ¬nh sau khi kiá»ƒm kÃª.
    Sinh SupplyTransaction vá»›i reason=Adjust Ä‘á»ƒ ghi log.
    """
    hub_id = _get_tech_hub(user)
    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Part category not found.")

    now = datetime.now(timezone.utc)
    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if not inv:
        if body.quantity_delta < 0:
            raise HTTPException(status_code=400, detail="Hub does not have this part item yet.")
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
                detail=f"Current stock is {inv.quantity}; adjusting by {body.quantity_delta} would result in a negative balance.",
            )
        inv.quantity = new_qty
        inv.updated_at = now

    db.add(SupplyTransaction(
        hub_id=hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity_delta, reason=SupplyTxnReason.ADJUST,
        performed_by=user.id,
        note=f"[Tech] Stocktake adjustment {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name}. "
             + (body.note or ""),
        created_at=now,
    ))
    await db.commit()
    await audit_supply_action(
        db, user, "STOCK_ADJUST", hub_id,
        f"Adjusted {body.quantity_delta:+d}x {cat.name} at Hub #{hub_id}",
        details={"hub_id": hub_id, "part_category_id": body.part_category_id, "quantity_delta": body.quantity_delta},
    )
    return {
        "message": f"Adjusted {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name}.",
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
    Technician ghi nháº­n linh kiá»‡n khÃ´ng cÃ²n sá»­ dá»¥ng Ä‘Æ°á»£c (vá»¡, há»ng, háº¿t háº¡n)
    â†’ trá»« tá»“n kho, log lÃ½ do.
    """
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Minimum discard quantity is 1.")
    hub_id = _get_tech_hub(user)
    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Part category not found.")

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
            detail=f"Only {avail} unit(s) remaining in stock, insufficient to discard {body.quantity}.",
        )

    now = datetime.now(timezone.utc)
    inv.quantity -= body.quantity
    inv.updated_at = now

    db.add(SupplyTransaction(
        hub_id=hub_id, part_category_id=body.part_category_id,
        quantity_delta=-body.quantity, reason=SupplyTxnReason.DISCARD,
        performed_by=user.id,
        note=f"[Tech] Discarded {body.quantity}x {cat.name}. " + (body.note or "Damaged part."),
        created_at=now,
    ))
    await db.commit()
    await audit_supply_action(
        db, user, "STOCK_DISCARD", hub_id,
        f"Recorded discard of {body.quantity}x {cat.name} at Hub #{hub_id}",
        details={"hub_id": hub_id, "part_category_id": body.part_category_id, "quantity": body.quantity},
    )
    return {
        "message": f"Recorded discard of {body.quantity}x {cat.name}.",
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
    Technician tá»± thiáº¿t láº­p ngÆ°á»¡ng cáº£nh bÃ¡o cho tá»«ng loáº¡i linh kiá»‡n táº¡i Hub mÃ¬nh.
    Khi quantity <= min_stock_threshold â†’ há»‡ thá»‘ng Ä‘Ã¡nh dáº¥u low_stock.
    """
    if body.min_stock_threshold < 0:
        raise HTTPException(status_code=400, detail="Threshold must be >= 0.")
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
    await audit_supply_action(
        db, user, "UPDATE_THRESHOLD", hub_id,
        f"Set min stock threshold to {body.min_stock_threshold} for part #{body.part_category_id} at Hub #{hub_id}",
        details={"hub_id": hub_id, "part_category_id": body.part_category_id, "min_stock_threshold": body.min_stock_threshold},
    )
    return {
        "message": f"Alert threshold set to {body.min_stock_threshold}.",
        "part_category_id": body.part_category_id,
        "min_stock_threshold": body.min_stock_threshold,
        "current_quantity": inv.quantity,
        "is_low_stock": inv.quantity <= body.min_stock_threshold,
    }


@router.get("/transactions", summary="Lá»‹ch sá»­ biáº¿n Ä‘á»™ng váº­t tÆ° táº¡i Hub cá»§a Technician")
async def get_my_hub_transactions(
    part_category_id: Optional[int] = Query(None),
    reason: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_tech),
):
    """Xem log táº¥t cáº£ biáº¿n Ä‘á»™ng váº­t tÆ° táº¡i Hub cá»§a mÃ¬nh (nháº­p, xuáº¥t, há»§y, Ä‘iá»u chá»‰nh)."""
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


