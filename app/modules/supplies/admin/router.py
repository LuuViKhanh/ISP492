"""
[Admin] Supplies — Quản lý linh kiện / vật tư tại Hub, duyệt đề xuất, báo cáo

Hệ thống không có kho trung tâm riêng — linh kiện quản lý trực tiếp tại Hub,
được cung cấp qua nhà cung cấp (supplier) khi cần.

Endpoints:
  GET  /admin/supplies/summary              — KPI tổng quan
  GET  /admin/supplies                      — số lượng vật tư toàn Hub
  GET  /admin/supplies/categories           — danh mục linh kiện
  POST /admin/supplies/categories           — tạo danh mục mới
  PATCH /admin/supplies/categories/{id}     — cập nhật danh mục
  POST /admin/supplies/receive              — nhập vật tư từ nhà cung cấp
  POST /admin/supplies/transfer             — điều phối giữa Hub
  GET  /admin/supplies/transactions         — log biến động
  GET  /admin/supplies/parts-requests       — danh sách đề xuất
  GET  /admin/supplies/parts-requests/{id}  — chi tiết đề xuất
  PATCH /admin/supplies/parts-requests/{id}/approve
  PATCH /admin/supplies/parts-requests/{id}/reject
  GET  /admin/supplies/finance-report       — báo cáo thu/chi
"""

from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import CurrentUser, RoleChecker
from app.shared.roles import UserRole
from app.modules.supplies.models import (
    PartCategory, PartCategoryType,
    HubSupply, SupplyTransaction, PartsRequest,
    PartsRequestStatus, SupplyTxnReason,
)
from app.modules.missions.models import Mission, MissionStatus
from app.modules.system.models import Hub
from app.modules.fleet.models import Battery

router = APIRouter(prefix="/admin/supplies", tags=["Admin - Supplies"])
allow_admin = RoleChecker([UserRole.ADMIN])

_COMPLETED_MS = (MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED)


# ── Schemas ────────────────────────────────────────────────────────

class CategoryCreate(BaseModel):
    name: str
    category_type: PartCategoryType
    compatible_drone_models: Optional[List[str]] = None
    unit_cost: Optional[float] = None
    description: Optional[str] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    unit_cost: Optional[float] = None
    description: Optional[str] = None
    compatible_drone_models: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ReceiveStockBody(BaseModel):
    hub_id: int
    part_category_id: int
    quantity: int
    unit_cost_override: Optional[float] = None
    note: Optional[str] = None


class TransferBody(BaseModel):
    from_hub_id: int
    to_hub_id: int
    part_category_id: int
    quantity: int
    note: Optional[str] = None


class ReviewBody(BaseModel):
    admin_note: Optional[str] = None


class CategoryOut(BaseModel):
    id: int
    name: str
    category_type: str
    unit_cost: Optional[float] = None
    compatible_drone_models: Optional[List[str]] = None
    description: Optional[str] = None
    is_active: bool

    class Config: from_attributes = True


class HubSupplyOut(BaseModel):
    hub_id: int
    hub_name: Optional[str] = None
    part_category_id: int
    part_name: str
    category_type: str
    unit_cost: Optional[float] = None
    compatible_drone_models: Optional[List[str]] = None
    quantity: int
    min_stock_threshold: int
    is_low_stock: bool
    updated_at: Optional[datetime] = None


class PartsRequestOut(BaseModel):
    id: int
    requested_by: str
    hub_id: int
    drone_id: Optional[str] = None
    part_category_id: int
    part_name: Optional[str] = None
    quantity_needed: int
    description: str
    photo_urls: Optional[List[str]] = None
    status: str
    admin_note: Optional[str] = None
    reviewed_by: Optional[str] = None
    actual_cost: Optional[float] = None
    hub_stock_available: Optional[int] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    fulfilled_at: Optional[datetime] = None
    class Config: from_attributes = True


class TxnOut(BaseModel):
    id: int
    hub_id: int
    part_category_id: int
    part_name: Optional[str] = None
    quantity_delta: int
    reason: str
    performed_by: Optional[str] = None
    reference_id: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime
    class Config: from_attributes = True


# ── Summary ────────────────────────────────────────────────────────

@router.get("/summary", summary="KPI tổng quan vật tư toàn hệ thống")
async def get_supplies_summary(
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """Trả về 4 chỉ số KPI đầu trang Supplies."""
    row = (await db.execute(
        select(
            select(func.coalesce(func.sum(HubSupply.quantity), 0)).scalar_subquery(),
            select(func.count()).select_from(HubSupply).where(HubSupply.quantity <= HubSupply.min_stock_threshold).scalar_subquery(),
            select(func.count()).select_from(PartsRequest).where(PartsRequest.status == PartsRequestStatus.PENDING).scalar_subquery(),
            select(func.count()).select_from(PartCategory).where(PartCategory.is_active == True).scalar_subquery(),
        )
    )).one()

    return {
        "total_items_in_stock": int(row[0] or 0),
        "low_stock_alerts": int(row[1] or 0),
        "pending_requests": int(row[2] or 0),
        "total_categories": int(row[3] or 0),
    }


# ── Categories ─────────────────────────────────────────────────────

@router.get("/categories", response_model=List[CategoryOut],
            summary="Danh mục linh kiện — ID, Name, Category Type, Unit Cost, Compatible Drone Models, Is Active")
async def list_categories(
    is_active: Optional[bool] = Query(None, description="Lọc theo is_active"),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = select(PartCategory).order_by(PartCategory.category_type, PartCategory.name)
    if is_active is not None:
        q = q.where(PartCategory.is_active == is_active)
    rows = (await db.execute(q)).scalars().all()
    return [
        CategoryOut(
            id=c.id, name=c.name,
            category_type=c.category_type.value if hasattr(c.category_type, "value") else str(c.category_type),
            unit_cost=c.unit_cost,
            compatible_drone_models=c.compatible_drone_models,
            description=c.description,
            is_active=c.is_active,
        ) for c in rows
    ]


@router.post("/categories", status_code=201, summary="Tạo danh mục linh kiện mới")
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    existing = (await db.execute(
        select(PartCategory).where(PartCategory.name == body.name)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Category '{body.name}' already exists.")
    cat = PartCategory(
        name=body.name, category_type=body.category_type,
        compatible_drone_models=body.compatible_drone_models,
        unit_cost=body.unit_cost, description=body.description,
        is_active=True, created_at=datetime.now(timezone.utc),
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.patch("/categories/{category_id}", summary="Cập nhật danh mục linh kiện")
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    cat = await db.get(PartCategory, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")
    if body.name is not None: cat.name = body.name
    if body.unit_cost is not None: cat.unit_cost = body.unit_cost
    if body.description is not None: cat.description = body.description
    if body.compatible_drone_models is not None: cat.compatible_drone_models = body.compatible_drone_models
    if body.is_active is not None: cat.is_active = body.is_active
    await db.commit()
    await db.refresh(cat)
    return cat


# ── Supplies (stock per hub) ────────────────────────────────────────

@router.get("", response_model=List[HubSupplyOut],
            summary="Tồn kho vật tư — Hub ID, Part Category ID, Quantity, Min Stock Threshold")
async def get_all_supplies(
    hub_id: Optional[int] = Query(None, description="Lọc theo Hub"),
    category_type: Optional[str] = Query(None, description="Lọc theo loại (Motor, Propeller...)"),
    low_stock_only: bool = Query(False, description="Chỉ hiện Hub sắp hết hàng"),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = (
        select(HubSupply, PartCategory, Hub)
        .join(PartCategory, HubSupply.part_category_id == PartCategory.id)
        .join(Hub, HubSupply.hub_id == Hub.id)
        .order_by(Hub.name, PartCategory.category_type, PartCategory.name)
    )
    if hub_id: q = q.where(HubSupply.hub_id == hub_id)
    if low_stock_only: q = q.where(HubSupply.quantity <= HubSupply.min_stock_threshold)
    if category_type: q = q.where(PartCategory.category_type == category_type)

    rows = (await db.execute(q)).all()
    return [
        HubSupplyOut(
            hub_id=hub.id, hub_name=hub.name,
            part_category_id=cat.id, part_name=cat.name,
            category_type=cat.category_type.value if hasattr(cat.category_type, "value") else str(cat.category_type),
            unit_cost=cat.unit_cost,
            compatible_drone_models=cat.compatible_drone_models,
            quantity=inv.quantity,
            min_stock_threshold=inv.min_stock_threshold,
            is_low_stock=inv.quantity <= inv.min_stock_threshold,
            updated_at=inv.updated_at,
        ) for inv, cat, hub in rows
    ]


@router.post("/receive", summary="Nhập vật tư từ nhà cung cấp vào Hub")
async def receive_stock(
    body: ReceiveStockBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Minimum receive quantity is 1.")
    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Part category not found.")
    hub = await db.get(Hub, body.hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found.")

    now = datetime.now(timezone.utc)
    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == body.hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if inv:
        inv.quantity += body.quantity
        inv.updated_at = now
    else:
        inv = HubSupply(
            hub_id=body.hub_id, part_category_id=body.part_category_id,
            quantity=body.quantity, min_stock_threshold=2, updated_at=now,
        )
        db.add(inv)

    unit_cost = body.unit_cost_override or cat.unit_cost
    total_cost = (unit_cost or 0) * body.quantity
    db.add(SupplyTransaction(
        hub_id=body.hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity, reason=SupplyTxnReason.RECEIVE,
        performed_by=user.id,
        note=f"Received {body.quantity}x {cat.name} at Hub #{body.hub_id}. "
             f"Unit cost: {unit_cost:,.0f} VND. Total: {total_cost:,.0f} VND. "
             + (body.note or ""),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Successfully received {body.quantity}x {cat.name} → Hub #{body.hub_id}.",
        "new_quantity": inv.quantity,
        "total_cost": total_cost,
    }


@router.post("/transfer", summary="Điều phối vật tư giữa các Hub")
async def transfer_stock(
    body: TransferBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Minimum transfer quantity is 1.")
    if body.from_hub_id == body.to_hub_id:
        raise HTTPException(status_code=400, detail="Source and destination hubs must be different.")

    now = datetime.now(timezone.utc)
    src = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == body.from_hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if not src or src.quantity < body.quantity:
        avail = src.quantity if src else 0
        raise HTTPException(
            status_code=400,
            detail=f"Hub #{body.from_hub_id} only has {avail} unit(s), insufficient for {body.quantity}.",
        )

    cat = await db.get(PartCategory, body.part_category_id)
    cat_name = cat.name if cat else f"#part_{body.part_category_id}"

    src.quantity -= body.quantity
    src.updated_at = now

    dst = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == body.to_hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if dst:
        dst.quantity += body.quantity
        dst.updated_at = now
    else:
        dst = HubSupply(
            hub_id=body.to_hub_id, part_category_id=body.part_category_id,
            quantity=body.quantity, min_stock_threshold=2, updated_at=now,
        )
        db.add(dst)

    ref = f"TRF_{body.from_hub_id}_to_{body.to_hub_id}_{int(now.timestamp())}"
    note_base = body.note or ""
    db.add(SupplyTransaction(
        hub_id=body.from_hub_id, part_category_id=body.part_category_id,
        quantity_delta=-body.quantity, reason=SupplyTxnReason.TRANSFER_OUT,
        performed_by=user.id, reference_id=ref,
        note=f"Transferred {body.quantity}x {cat_name} → Hub #{body.to_hub_id}. {note_base}",
        created_at=now,
    ))
    db.add(SupplyTransaction(
        hub_id=body.to_hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity, reason=SupplyTxnReason.TRANSFER_IN,
        performed_by=user.id, reference_id=ref,
        note=f"Received transfer {body.quantity}x {cat_name} from Hub #{body.from_hub_id}. {note_base}",
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Successfully transferred {body.quantity}x {cat_name}: Hub #{body.from_hub_id} → Hub #{body.to_hub_id}.",
        "from_hub_new_qty": src.quantity,
        "to_hub_new_qty": dst.quantity,
    }


# ── Transactions ────────────────────────────────────────────────────

@router.get("/transactions", response_model=List[TxnOut],
            summary="Log biến động vật tư toàn hệ thống")
async def list_transactions(
    hub_id: Optional[int] = Query(None),
    part_category_id: Optional[int] = Query(None),
    reason: Optional[str] = Query(None, description="Receive / RepairUse / TransferIn / TransferOut..."),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = select(SupplyTransaction).order_by(SupplyTransaction.created_at.desc()).limit(limit)
    if hub_id: q = q.where(SupplyTransaction.hub_id == hub_id)
    if part_category_id: q = q.where(SupplyTransaction.part_category_id == part_category_id)
    if reason: q = q.where(SupplyTransaction.reason == reason)

    rows = (await db.execute(q)).scalars().all()
    cat_ids = list({r.part_category_id for r in rows})
    cats = {}
    if cat_ids:
        cats = {c.id: c.name for c in (await db.execute(
            select(PartCategory).where(PartCategory.id.in_(cat_ids))
        )).scalars().all()}

    result = []
    for r in rows:
        out = TxnOut.model_validate(r)
        out.part_name = cats.get(r.part_category_id)
        result.append(out)
    return result


# ── Parts Requests ──────────────────────────────────────────────────

@router.get("/parts-requests", response_model=List[PartsRequestOut],
            summary="Danh sách đề xuất linh kiện")
async def list_all_requests(
    status: Optional[str] = Query(None),
    hub_id: Optional[int] = Query(None),
    drone_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = select(PartsRequest).order_by(PartsRequest.created_at.desc()).limit(limit)
    if status: q = q.where(PartsRequest.status == status)
    if hub_id: q = q.where(PartsRequest.hub_id == hub_id)
    if drone_id: q = q.where(PartsRequest.drone_id == drone_id)

    rows = (await db.execute(q)).scalars().all()
    cat_ids = list({r.part_category_id for r in rows})
    cats, inv_map = {}, {}
    if cat_ids:
        cats = {c.id: c.name for c in (await db.execute(
            select(PartCategory).where(PartCategory.id.in_(cat_ids))
        )).scalars().all()}
    if rows:
        hub_ids_q = list({r.hub_id for r in rows})
        part_ids_q = list({r.part_category_id for r in rows})
        inv_map = {(i.hub_id, i.part_category_id): i.quantity for i in (await db.execute(
            select(HubSupply).where(and_(
                HubSupply.hub_id.in_(hub_ids_q),
                HubSupply.part_category_id.in_(part_ids_q),
            ))
        )).scalars().all()}

    result = []
    for r in rows:
        out = PartsRequestOut.model_validate(r)
        out.part_name = cats.get(r.part_category_id)
        out.hub_stock_available = inv_map.get((r.hub_id, r.part_category_id))
        result.append(out)
    return result


@router.get("/parts-requests/{request_id}", response_model=PartsRequestOut,
            summary="Chi tiết một đề xuất linh kiện")
async def get_request_detail(
    request_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Parts request not found.")
    cat = await db.get(PartCategory, req.part_category_id)
    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == req.hub_id,
            HubSupply.part_category_id == req.part_category_id,
        ))
    )).scalar_one_or_none()
    out = PartsRequestOut.model_validate(req)
    out.part_name = cat.name if cat else None
    out.hub_stock_available = inv.quantity if inv else 0
    return out


@router.patch("/parts-requests/{request_id}/approve",
              summary="Duyệt đề xuất linh kiện")
async def approve_request(
    request_id: int,
    body: ReviewBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Parts request not found.")
    if req.status not in (PartsRequestStatus.PENDING, PartsRequestStatus.WAITING_STOCK):
        raise HTTPException(status_code=400,
            detail=f"Status '{req.status.value}' cannot be approved.")

    now = datetime.now(timezone.utc)
    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == req.hub_id,
            HubSupply.part_category_id == req.part_category_id,
        ))
    )).scalar_one_or_none()

    req.reviewed_by = user.id
    req.reviewed_at = now
    req.admin_note = body.admin_note

    if inv and inv.quantity >= req.quantity_needed:
        req.status = PartsRequestStatus.APPROVED
        inv.quantity -= req.quantity_needed
        inv.updated_at = now
        cat = await db.get(PartCategory, req.part_category_id)
        db.add(SupplyTransaction(
            hub_id=req.hub_id, part_category_id=req.part_category_id,
            quantity_delta=-req.quantity_needed, reason=SupplyTxnReason.REPAIR_USE,
            performed_by=user.id, reference_id=str(req.id),
            note=f"Admin approved request #{req.id}: {req.quantity_needed}x "
                 f"{cat.name if cat else '?'} for drone {req.drone_id or 'N/A'}",
            created_at=now,
        ))
        # NOTE: Parts request approval — no in-app notification to Tech.
        # Tech is notified via maintenance alerts and mission scheduling events.

        return {"message": "Approved; parts have been deducted from hub stock.",
                "request_id": request_id, "status": PartsRequestStatus.APPROVED.value,
                "remaining_stock": inv.quantity}
    else:
        req.status = PartsRequestStatus.WAITING_STOCK
        await db.commit()
        avail = inv.quantity if inv else 0
        return {"message": f"WAITING_STOCK — Hub #{req.hub_id} only has {avail} "
                           f"(requires {req.quantity_needed}). Please transfer stock from another hub.",
                "request_id": request_id, "status": PartsRequestStatus.WAITING_STOCK.value,
                "hub_stock_available": avail}


@router.patch("/parts-requests/{request_id}/reject",
              summary="Từ chối đề xuất linh kiện")
async def reject_request(
    request_id: int,
    body: ReviewBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Parts request not found.")
    if req.status in (PartsRequestStatus.FULFILLED, PartsRequestStatus.REJECTED):
        raise HTTPException(status_code=400, detail="Parts request has already been finalized and cannot be rejected.")
    now = datetime.now(timezone.utc)
    req.status = PartsRequestStatus.REJECTED
    req.reviewed_by = user.id
    req.reviewed_at = now
    req.admin_note = body.admin_note or "Rejected by Admin."
    await db.commit()

    # NOTE: No in-app notification for reject — Tech sees status change in app.
    return {"message": "Parts request rejected.", "request_id": request_id}


# ── Finance Report ──────────────────────────────────────────────────

@router.get("/finance-report", summary="Báo cáo thu/chi linh kiện & doanh thu")
async def finance_report(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2020),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """Thu = doanh thu giao hàng. Chi = chi phí linh kiện (nhập kho + sửa chữa)."""
    now = datetime.now(timezone.utc)
    target_year = year or now.year
    if month:
        from calendar import monthrange
        _, last_day = monthrange(target_year, month)
        p_start = datetime(target_year, month, 1, tzinfo=timezone.utc)
        p_end   = datetime(target_year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        label   = f"Month {month:02d}/{target_year}"
    else:
        p_start = datetime(target_year, 1, 1, tzinfo=timezone.utc)
        p_end   = datetime(target_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        label   = f"Year {target_year}"

    # Thu
    comp_ts = func.coalesce(Mission.arrival_confirmed_at, Mission.end_time, Mission.departed_at)
    total_revenue = float((await db.execute(
        select(func.coalesce(func.sum(Mission.delivery_fee), 0.0))
        .where(and_(
            Mission.status.in_(_COMPLETED_MS),
            comp_ts >= p_start.replace(tzinfo=None),
            comp_ts <= p_end.replace(tzinfo=None),
        ))
    )).scalar() or 0.0)

    # Chi: sửa chữa + nhập kho tổng hợp theo category trực tiếp bằng SQL GROUP BY
    repair_rows = (await db.execute(
        select(PartCategory.name, func.coalesce(func.sum(PartsRequest.actual_cost), 0.0))
        .join(PartCategory, PartsRequest.part_category_id == PartCategory.id)
        .where(and_(
            PartsRequest.status == PartsRequestStatus.FULFILLED,
            PartsRequest.fulfilled_at >= p_start,
            PartsRequest.fulfilled_at <= p_end,
            PartsRequest.actual_cost.isnot(None),
        ))
        .group_by(PartCategory.name)
    )).all()
    repair_cost = sum(float(cost or 0.0) for _, cost in repair_rows)

    receive_rows = (await db.execute(
        select(
            PartCategory.name,
            func.coalesce(func.sum(func.coalesce(PartCategory.unit_cost, 0.0) * SupplyTransaction.quantity_delta), 0.0),
        )
        .join(PartCategory, SupplyTransaction.part_category_id == PartCategory.id)
        .where(and_(
            SupplyTransaction.reason == SupplyTxnReason.RECEIVE,
            SupplyTransaction.created_at >= p_start,
            SupplyTransaction.created_at <= p_end,
        ))
        .group_by(PartCategory.name)
    )).all()
    receive_cost = sum(float(cost or 0.0) for _, cost in receive_rows)

    cat_expense: dict[str, float] = {}
    for cat_name, cost in receive_rows:
        cat_expense[cat_name] = cat_expense.get(cat_name, 0.0) + float(cost or 0.0)
    for cat_name, cost in repair_rows:
        cat_expense[cat_name] = cat_expense.get(cat_name, 0.0) + float(cost or 0.0)

    total_expense = repair_cost + receive_cost
    net = total_revenue - total_expense

    counts_row = (await db.execute(
        select(
            select(func.count()).select_from(PartsRequest).where(and_(
                PartsRequest.created_at >= p_start, PartsRequest.created_at <= p_end,
            )).scalar_subquery(),
            select(func.count()).select_from(PartsRequest).where(and_(
                PartsRequest.status == PartsRequestStatus.FULFILLED,
                PartsRequest.fulfilled_at >= p_start, PartsRequest.fulfilled_at <= p_end,
            )).scalar_subquery(),
        )
    )).one()
    total_requests = int(counts_row[0] or 0)
    fulfilled_count = int(counts_row[1] or 0)

    return {
        "period": label,
        "revenue": {"delivery_fees": round(total_revenue, 2), "total_income": round(total_revenue, 2)},
        "expense": {
            "parts_procurement": round(receive_cost, 2),
            "parts_repair_use": round(repair_cost, 2),
            "total_expense": round(total_expense, 2),
            "by_category": {k: round(v, 2) for k, v in sorted(cat_expense.items(), key=lambda x: -x[1])},
        },
        "net_cashflow": round(net, 2),
        "summary": {
            "total_parts_requests": total_requests,
            "fulfilled_requests": fulfilled_count,
            "profit_margin_pct": round(net / total_revenue * 100, 1) if total_revenue > 0 else None,
        },
    }


# ══════════════════════════════════════════════════════════════════
# HUB-LEVEL SUPPLY MANAGEMENT
# Quản lý spare part tại từng Hub cụ thể
# ══════════════════════════════════════════════════════════════════

class AdjustBody(BaseModel):
    part_category_id: int
    quantity_delta: int          # dương = thêm, âm = bớt
    reason: SupplyTxnReason = SupplyTxnReason.ADJUST
    note: Optional[str] = None


class DiscardBody(BaseModel):
    part_category_id: int
    quantity: int                # số lượng hủy bỏ (phải > 0)
    note: Optional[str] = None  # lý do hủy (vỡ, hỏng, hết hạn...)


class ThresholdBody(BaseModel):
    part_category_id: int
    min_stock_threshold: int


class BulkReceiveItem(BaseModel):
    part_category_id: int
    quantity: int
    unit_cost_override: Optional[float] = None
    note: Optional[str] = None


class BulkReceiveBody(BaseModel):
    hub_id: int
    items: List[BulkReceiveItem]


@router.get("/hubs/{hub_id}", summary="Chi tiết vật tư tại một Hub")
async def get_hub_supplies_detail(
    hub_id: int,
    low_stock_only: bool = Query(False),
    category_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Xem toàn bộ spare part tại Hub cụ thể, kèm thông tin tồn kho,
    ngưỡng cảnh báo và giá từng loại.
    """
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found.")

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

    # Tổng giá trị tồn kho tại Hub
    total_value = sum(
        (cat.unit_cost or 0) * inv.quantity for inv, cat in rows
    )

    return {
        "hub_id": hub.id,
        "hub_name": hub.name,
        "hub_address": hub.address,
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
                "stock_value": round((cat.unit_cost or 0) * inv.quantity, 0),
                "updated_at": inv.updated_at.isoformat() if inv.updated_at else None,
            }
            for inv, cat in rows
        ],
    }


@router.post("/hubs/{hub_id}/adjust", summary="Điều chỉnh số lượng vật tư tại Hub (kiểm kê thực tế)")
async def adjust_hub_supply(
    hub_id: int,
    body: AdjustBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Điều chỉnh số lượng thực tế sau khi kiểm kê.
    - `quantity_delta` > 0: tìm thấy thêm / nhập bổ sung
    - `quantity_delta` < 0: thực tế ít hơn hồ sơ / mất mát
    """
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found.")
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
            raise HTTPException(status_code=400, detail="Hub does not have this part item yet; cannot deduct.")
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
                detail=f"Insufficient stock. Current quantity is {inv.quantity}; adjusting by -{abs(body.quantity_delta)} would result in a negative balance.",
            )
        inv.quantity = new_qty
        inv.updated_at = now

    db.add(SupplyTransaction(
        hub_id=hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity_delta, reason=body.reason,
        performed_by=user.id,
        note=f"[Admin] Stocktake adjustment {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name}. "
             + (body.note or ""),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Adjusted {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name} at Hub #{hub_id}.",
        "new_quantity": inv.quantity,
        "hub_id": hub_id,
    }


@router.post("/hubs/{hub_id}/discard", summary="Hủy bỏ linh kiện hỏng / hết hạn tại Hub")
async def discard_hub_supply(
    hub_id: int,
    body: DiscardBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Ghi nhận linh kiện hỏng, vỡ hoặc hết hạn sử dụng → trừ khỏi tồn kho,
    ghi log lý do.
    """
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Minimum discard quantity is 1.")

    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found.")
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
        note=f"[Admin] Discarded {body.quantity}x {cat.name} at Hub #{hub_id}. "
             + (body.note or "Damaged / expired part."),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Discarded {body.quantity}x {cat.name} at Hub #{hub_id}.",
        "remaining_quantity": inv.quantity,
    }


@router.patch("/hubs/{hub_id}/threshold", summary="Cập nhật ngưỡng cảnh báo tồn kho tại Hub")
async def set_hub_threshold(
    hub_id: int,
    body: ThresholdBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Cập nhật min_stock_threshold cho một loại linh kiện tại Hub.
    Khi quantity <= threshold → hiện cảnh báo low_stock.
    """
    if body.min_stock_threshold < 0:
        raise HTTPException(status_code=400, detail="Minimum threshold must be >= 0.")

    inv = (await db.execute(
        select(HubSupply).where(and_(
            HubSupply.hub_id == hub_id,
            HubSupply.part_category_id == body.part_category_id,
        ))
    )).scalar_one_or_none()

    if not inv:
        # Tạo mới entry với qty=0 nếu chưa tồn tại
        inv = HubSupply(
            hub_id=hub_id, part_category_id=body.part_category_id,
            quantity=0, min_stock_threshold=body.min_stock_threshold,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(inv)
    else:
        inv.min_stock_threshold = body.min_stock_threshold
        inv.updated_at = datetime.now(timezone.utc)

    await db.commit()
    return {
        "message": f"Alert threshold updated to {body.min_stock_threshold}.",
        "hub_id": hub_id,
        "part_category_id": body.part_category_id,
        "min_stock_threshold": body.min_stock_threshold,
        "current_quantity": inv.quantity,
        "is_low_stock": inv.quantity <= body.min_stock_threshold,
    }


@router.post("/hubs/{hub_id}/bulk-receive", summary="Nhập nhiều loại vật tư cùng lúc vào Hub")
async def bulk_receive(
    hub_id: int,
    body: BulkReceiveBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Nhập nhiều loại linh kiện từ nhà cung cấp trong một request.
    Hữu ích khi nhận shipment gồm nhiều mặt hàng cùng lúc.
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="Items list cannot be empty.")

    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found.")

    now = datetime.now(timezone.utc)
    results = []
    total_cost = 0.0

    for item in body.items:
        if item.quantity < 1:
            raise HTTPException(status_code=400, detail=f"Quantity for part #{item.part_category_id} must be >= 1.")

    # [PERF NOTE] Nạp batch PartCategory và HubSupply theo IN (...) trước vòng lặp để tránh 2*N roundtrip tới DB.
    item_cat_ids = {item.part_category_id for item in body.items}
    cats_by_id = {
        c.id: c
        for c in (await db.execute(select(PartCategory).where(PartCategory.id.in_(item_cat_ids)))).scalars().all()
    }
    supply_by_cat = {
        inv.part_category_id: inv
        for inv in (
            await db.execute(
                select(HubSupply).where(
                    and_(
                        HubSupply.hub_id == hub_id,
                        HubSupply.part_category_id.in_(item_cat_ids),
                    )
                )
            )
        ).scalars().all()
    }

    for item in body.items:
        cat = cats_by_id.get(item.part_category_id)
        if not cat:
            raise HTTPException(status_code=404, detail=f"Category #{item.part_category_id} not found.")

        inv = supply_by_cat.get(item.part_category_id)
        if inv:
            inv.quantity += item.quantity
            inv.updated_at = now
        else:
            inv = HubSupply(
                hub_id=hub_id, part_category_id=item.part_category_id,
                quantity=item.quantity, min_stock_threshold=2, updated_at=now,
            )
            db.add(inv)
            supply_by_cat[item.part_category_id] = inv

        unit_cost = item.unit_cost_override or cat.unit_cost or 0
        line_cost = unit_cost * item.quantity
        total_cost += line_cost

        db.add(SupplyTransaction(
            hub_id=hub_id, part_category_id=item.part_category_id,
            quantity_delta=item.quantity, reason=SupplyTxnReason.RECEIVE,
            performed_by=user.id,
            note=f"[Bulk] Received {item.quantity}x {cat.name}. Unit cost: {unit_cost:,.0f} VND. "
                 + (item.note or ""),
            created_at=now,
        ))
        results.append({
            "part_category_id": cat.id,
            "part_name": cat.name,
            "quantity_added": item.quantity,
            "new_quantity": inv.quantity,
            "line_cost": round(line_cost, 0),
        })

    await db.commit()
    return {
        "message": f"Successfully received {len(results)} part type(s) into Hub #{hub_id}.",
        "hub_id": hub_id,
        "hub_name": hub.name,
        "items": results,
        "total_cost": round(total_cost, 0),
    }


# ══════════════════════════════════════════════════════════════════
# REPLACEMENT REQUESTS (Admin)
# Danh sách, chi tiết, Confirm Supply, Reject
# ══════════════════════════════════════════════════════════════════

from app.modules.supplies.models import (
    ReplacementRequest, ReplacementType, ReplacementStatus,
)
from app.modules.fleet.models import Battery, batteries_status


class ReplacementRequestOut(BaseModel):
    request_id: int
    type: str
    damaged_item_id: Optional[str] = None
    part_category_id: Optional[int] = None
    part_name: Optional[str] = None
    quantity: int
    reason: str
    status: str
    hub_id: int
    requested_by: str
    photo_urls: Optional[List[str]] = None
    supplied_item_id: Optional[str] = None
    supplied_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    hub_stock_available: Optional[int] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None

    class Config: from_attributes = True


class SupplyBody(BaseModel):
    supplied_item_id: Optional[str] = None  # battery_id nếu type=BATTERY, bỏ qua nếu type=PART
    admin_note: Optional[str] = None


class RejectBody(BaseModel):
    rejection_reason: str


@router.get("/replacement-requests", response_model=List[ReplacementRequestOut],
            summary="Danh sách tất cả Replacement Requests")
async def list_all_replacement_requests(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    hub_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = select(ReplacementRequest).order_by(
        ReplacementRequest.created_at.desc()
    ).limit(limit)
    if status: q = q.where(ReplacementRequest.status == status)
    if type:   q = q.where(ReplacementRequest.type == type)
    if hub_id: q = q.where(ReplacementRequest.hub_id == hub_id)

    rows = (await db.execute(q)).scalars().all()

    cat_ids = list({r.part_category_id for r in rows if r.part_category_id})
    cats = {}
    if cat_ids:
        cats = {c.id: c.name for c in (await db.execute(
            select(PartCategory).where(PartCategory.id.in_(cat_ids))
        )).scalars().all()}

    # Tồn kho hiện tại cho PART requests
    inv_map = {}
    part_requests = [(r.hub_id, r.part_category_id) for r in rows
                     if r.type == ReplacementType.PART and r.part_category_id]
    if part_requests:
        hub_ids_q = list({p[0] for p in part_requests})
        pid_q = list({p[1] for p in part_requests})
        inv_map = {(i.hub_id, i.part_category_id): i.quantity
                   for i in (await db.execute(
                       select(HubSupply).where(and_(
                           HubSupply.hub_id.in_(hub_ids_q),
                           HubSupply.part_category_id.in_(pid_q),
                       ))
                   )).scalars().all()}

    return [
        ReplacementRequestOut(
            request_id=r.id, type=r.type.value, damaged_item_id=r.damaged_item_id,
            part_category_id=r.part_category_id, part_name=cats.get(r.part_category_id),
            quantity=r.quantity, reason=r.reason, status=r.status.value,
            hub_id=r.hub_id, requested_by=r.requested_by,
            photo_urls=r.photo_urls, supplied_item_id=r.supplied_item_id,
            supplied_at=r.supplied_at, rejection_reason=r.rejection_reason,
            hub_stock_available=inv_map.get((r.hub_id, r.part_category_id)),
            created_at=r.created_at, reviewed_at=r.reviewed_at,
        ) for r in rows
    ]


@router.get("/replacement-requests/{request_id}", response_model=ReplacementRequestOut,
            summary="Chi tiết Replacement Request")
async def get_replacement_request(
    request_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    req = await db.get(ReplacementRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Yêu cầu không tồn tại.")
    cat = await db.get(PartCategory, req.part_category_id) if req.part_category_id else None
    stock = None
    if req.type == ReplacementType.PART and req.part_category_id:
        inv = (await db.execute(
            select(HubSupply).where(and_(
                HubSupply.hub_id == req.hub_id,
                HubSupply.part_category_id == req.part_category_id,
            ))
        )).scalar_one_or_none()
        stock = inv.quantity if inv else 0
    return ReplacementRequestOut(
        request_id=req.id, type=req.type.value, damaged_item_id=req.damaged_item_id,
        part_category_id=req.part_category_id, part_name=cat.name if cat else None,
        quantity=req.quantity, reason=req.reason, status=req.status.value,
        hub_id=req.hub_id, requested_by=req.requested_by,
        photo_urls=req.photo_urls, supplied_item_id=req.supplied_item_id,
        supplied_at=req.supplied_at, rejection_reason=req.rejection_reason,
        hub_stock_available=stock, created_at=req.created_at, reviewed_at=req.reviewed_at,
    )


@router.patch("/replacement-requests/{request_id}/supply",
              summary="Confirm Supply — cấp phát pin hoặc linh kiện")
async def confirm_supply(
    request_id: int,
    body: SupplyBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Admin xác nhận cấp phát:

    - **type=BATTERY**: `supplied_item_id` = battery_id được cấp. Cập nhật battery.current_hub_id.
    - **type=PART**: trừ tồn kho HubSupply, ghi SupplyTransaction.
    - Chỉ xử lý khi status = AWAITING_SUPPLY → tránh trừ kho 2 lần.
    - Gửi notification cho Technician sau khi cấp phát.
    """
    req = await db.get(ReplacementRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Yêu cầu không tồn tại.")
    if req.status != ReplacementStatus.AWAITING_SUPPLY:
        raise HTTPException(
            status_code=400,
            detail=f"Yêu cầu đang ở trạng thái '{req.status.value}', không thể cấp phát."
        )

    now = datetime.now(timezone.utc)

    if req.type == ReplacementType.BATTERY:
        # Validate battery tồn tại và ở đúng hub
        bat_id = body.supplied_item_id
        if not bat_id:
            raise HTTPException(status_code=400, detail="Cần truyền supplied_item_id (battery_id) cho BATTERY request.")
        bat = await db.get(Battery, bat_id)
        if not bat:
            raise HTTPException(status_code=404, detail=f"Battery '{bat_id}' không tồn tại.")
        if bat.drone_id:
            raise HTTPException(status_code=400, detail=f"Battery '{bat_id}' đang gắn trên drone, không thể cấp phát.")
        # Chuyển battery về hub yêu cầu
        bat.current_hub_id = req.hub_id
        bat.update_at = now
        req.supplied_item_id = bat_id

    elif req.type == ReplacementType.PART:
        if not req.part_category_id:
            raise HTTPException(status_code=400, detail="part_category_id không tồn tại trên yêu cầu này.")
        # Kiểm tra và trừ tồn kho
        inv = (await db.execute(
            select(HubSupply).where(and_(
                HubSupply.hub_id == req.hub_id,
                HubSupply.part_category_id == req.part_category_id,
            ))
        )).scalar_one_or_none()

        if not inv or inv.quantity < req.quantity:
            avail = inv.quantity if inv else 0
            raise HTTPException(
                status_code=400,
                detail=f"Tồn kho Hub #{req.hub_id} chỉ còn {avail} đơn vị, cần {req.quantity}."
            )
        inv.quantity -= req.quantity
        inv.updated_at = now
        req.supplied_item_id = str(req.part_category_id)

        # Ghi SupplyTransaction
        cat = await db.get(PartCategory, req.part_category_id)
        db.add(SupplyTransaction(
            hub_id=req.hub_id,
            part_category_id=req.part_category_id,
            quantity_delta=-req.quantity,
            reason=SupplyTxnReason.REPAIR_USE,
            performed_by=user.id,
            reference_id=f"REPLACEMENT_{req.id}",
            note=f"Admin cấp phát cho replacement request #{req.id}: "
                 f"{req.quantity}x {cat.name if cat else '?'}",
            created_at=now,
        ))

    # Cập nhật request
    req.status = ReplacementStatus.SUPPLIED
    req.reviewed_by = user.id
    req.reviewed_at = now
    req.supplied_at = now

    await db.commit()

    # NOTE: Replacement supply/reject — no in-app notification.
    # Tech is notified via maintenance alerts and mission scheduling events.

    return {
        "message": f"Đã cấp phát thành công.",
        "request_id": request_id,
        "status": ReplacementStatus.SUPPLIED.value,
        "supplied_item_id": req.supplied_item_id,
        "supplied_at": req.supplied_at.isoformat(),
    }


@router.patch("/replacement-requests/{request_id}/reject",
              summary="Từ chối Replacement Request")
async def reject_replacement_request(
    request_id: int,
    body: RejectBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    req = await db.get(ReplacementRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Yêu cầu không tồn tại.")
    if req.status != ReplacementStatus.AWAITING_SUPPLY:
        raise HTTPException(
            status_code=400,
            detail=f"Yêu cầu đang ở trạng thái '{req.status.value}', không thể từ chối."
        )

    now = datetime.now(timezone.utc)
    req.status = ReplacementStatus.REJECTED
    req.reviewed_by = user.id
    req.reviewed_at = now
    req.rejection_reason = body.rejection_reason
    await db.commit()

    # NOTE: No in-app notification for replacement reject.
    return {
        "message": "Đã từ chối yêu cầu.",
        "request_id": request_id,
        "status": ReplacementStatus.REJECTED.value,
        "rejection_reason": body.rejection_reason,
    }


# ══════════════════════════════════════════════════════════════════
# FINANCIAL OVERVIEW
# Thu chi đầy đủ: doanh thu thực tế, hoàn tiền, nhập pin, nhập linh kiện
# GET /admin/supplies/financial-overview
# ══════════════════════════════════════════════════════════════════

from app.modules.missions.models import Order, PaymentStatus
from app.modules.fleet.models import Battery, BatteryModelCatalog


@router.get("/financial-overview", summary="Financial Overview — Thu chi tổng hợp")
async def financial_overview(
    month: Optional[int] = Query(None, ge=1, le=12, description="Tháng (1-12)"),
    year:  Optional[int] = Query(None, ge=2020,     description="Năm"),
    db:   AsyncSession = Depends(get_async_db),
    user: CurrentUser  = Depends(allow_admin),
):
    """
    **Financial Overview** — tổng hợp thu/chi đầy đủ theo kỳ.

    **Thu:**
    - `paid_revenue` — tổng doanh thu từ các đơn đã thanh toán (payment_status=PAID)

    **Hoàn trả:**
    - `completed_refunds` — tổng số tiền đã hoàn cho khách (refund_amount)

    **Chi phí nhập hàng (Acquisition Cost — chỉ ghi nhận khi nhập, không tính khi sử dụng):**
    - `battery_acquisition_cost` — tổng chi phí pin nhập vào hệ thống (batteries.create_at × unit_cost từ catalog)
    - `parts_acquisition_cost` — tổng chi phí linh kiện nhập kho (supply_transactions reason=Receive × unit_cost)

    **Lịch sử:**
    - `transaction_history` — danh sách tất cả khoản thu/chi trong kỳ, sắp xếp theo thời gian
    """
    now_utc = datetime.now(timezone.utc)
    target_year = year or now_utc.year

    if month:
        from calendar import monthrange
        _, last_day = monthrange(target_year, month)
        p_start = datetime(target_year, month, 1, tzinfo=timezone.utc)
        p_end   = datetime(target_year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        label   = f"Tháng {month:02d}/{target_year}"
    else:
        p_start = datetime(target_year, 1, 1, tzinfo=timezone.utc)
        p_end   = datetime(target_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        label   = f"Năm {target_year}"

    p_start_naive = p_start.replace(tzinfo=None)
    p_end_naive   = p_end.replace(tzinfo=None)

    # ── 1. PAID REVENUE ──────────────────────────────────────────
    paid_orders = (await db.execute(
        select(Order).where(and_(
            Order.payment_status == PaymentStatus.PAID,
            Order.paid_at >= p_start_naive,
            Order.paid_at <= p_end_naive,
        ))
    )).scalars().all()

    paid_revenue = sum(o.delivery_fee or 0 for o in paid_orders)

    # ── 2. COMPLETED REFUNDS ─────────────────────────────────────
    refunded_orders = (await db.execute(
        select(Order).where(and_(
            Order.payment_status == PaymentStatus.REFUNDED,
            Order.refund_at >= p_start_naive,
            Order.refund_at <= p_end_naive,
            Order.refund_amount.isnot(None),
        ))
    )).scalars().all()

    completed_refunds = sum(o.refund_amount or 0 for o in refunded_orders)

    # ── 3. BATTERY ACQUISITION COST ──────────────────────────────
    # Chỉ ghi nhận khi nhập pin mới (create_at trong kỳ)
    # Tra unit_cost từ BatteryModelCatalog theo battery_model
    batteries_in_period = (await db.execute(
        select(Battery).where(and_(
            Battery.create_at >= p_start,
            Battery.create_at <= p_end,
        ))
    )).scalars().all()

    # Load catalog để tra giá
    catalog_rows = (await db.execute(select(BatteryModelCatalog))).scalars().all()
    catalog_map = {c.name: c.unit_cost for c in catalog_rows}

    battery_acquisition_cost = sum(
        catalog_map.get(b.battery_model, 0) or 0
        for b in batteries_in_period
    )

    # ── 4. PARTS ACQUISITION COST ────────────────────────────────
    # Chỉ ghi nhận khi Admin nhập kho (reason=Receive), không tính khi sử dụng
    receive_txns = (await db.execute(
        select(SupplyTransaction, PartCategory)
        .join(PartCategory, SupplyTransaction.part_category_id == PartCategory.id)
        .where(and_(
            SupplyTransaction.reason == SupplyTxnReason.RECEIVE,
            SupplyTransaction.created_at >= p_start,
            SupplyTransaction.created_at <= p_end,
        ))
    )).all()

    parts_acquisition_cost = sum(
        (cat.unit_cost or 0) * txn.quantity_delta
        for txn, cat in receive_txns
    )

    # ── 5. SUMMARY ───────────────────────────────────────────────
    total_income  = paid_revenue
    total_expense = battery_acquisition_cost + parts_acquisition_cost
    net_cashflow  = total_income - completed_refunds - total_expense

    # ── 6. TRANSACTION HISTORY ───────────────────────────────────
    history = []

    # Income: paid orders
    for o in sorted(paid_orders, key=lambda x: x.paid_at or datetime.min):
        history.append({
            "date": o.paid_at.isoformat() if o.paid_at else None,
            "type": "INCOME",
            "category": "Delivery Fee",
            "reference_id": o.id,
            "description": f"Đơn hàng #{o.id} đã thanh toán",
            "amount": round(o.delivery_fee or 0, 0),
        })

    # Refunds
    for o in sorted(refunded_orders, key=lambda x: x.refund_at or datetime.min):
        history.append({
            "date": o.refund_at.isoformat() if o.refund_at else None,
            "type": "REFUND",
            "category": "Customer Refund",
            "reference_id": o.id,
            "description": f"Hoàn tiền đơn #{o.id}: {o.refund_reason or 'N/A'}",
            "amount": -round(o.refund_amount or 0, 0),
        })

    # Battery acquisitions
    for b in sorted(batteries_in_period, key=lambda x: x.create_at or datetime.min):
        cost = catalog_map.get(b.battery_model, 0) or 0
        if cost > 0:
            history.append({
                "date": b.create_at.isoformat() if b.create_at else None,
                "type": "EXPENSE",
                "category": "Battery Acquisition",
                "reference_id": b.id,
                "description": f"Nhập pin {b.battery_model or 'N/A'} — #{b.id}",
                "amount": -round(cost, 0),
            })

    # Parts acquisitions
    for txn, cat in sorted(receive_txns, key=lambda x: x[0].created_at):
        line_cost = (cat.unit_cost or 0) * txn.quantity_delta
        if line_cost > 0:
            history.append({
                "date": txn.created_at.isoformat(),
                "type": "EXPENSE",
                "category": "Parts Acquisition",
                "reference_id": f"TXN-{txn.id}",
                "description": f"Nhập kho {txn.quantity_delta}x {cat.name} tại Hub #{txn.hub_id}",
                "amount": -round(line_cost, 0),
            })

    # Sort by date desc
    history.sort(key=lambda x: x["date"] or "", reverse=True)

    # ── 7. BREAKDOWN BY CATEGORY ─────────────────────────────────
    parts_by_cat: dict[str, float] = {}
    for txn, cat in receive_txns:
        cost = (cat.unit_cost or 0) * txn.quantity_delta
        parts_by_cat[cat.name] = parts_by_cat.get(cat.name, 0) + cost

    battery_by_model: dict[str, float] = {}
    for b in batteries_in_period:
        cost = catalog_map.get(b.battery_model, 0) or 0
        if b.battery_model and cost > 0:
            battery_by_model[b.battery_model] = battery_by_model.get(b.battery_model, 0) + cost

    return {
        "period": label,

        # ── Thu ──────────────────────────────────────────────────
        "income": {
            "paid_revenue": round(paid_revenue, 0),
            "paid_orders_count": len(paid_orders),
        },

        # ── Hoàn trả ─────────────────────────────────────────────
        "refunds": {
            "completed_refunds": round(completed_refunds, 0),
            "refunded_orders_count": len(refunded_orders),
        },

        # ── Chi phí nhập hàng ────────────────────────────────────
        "acquisition_costs": {
            "battery_acquisition_cost": round(battery_acquisition_cost, 0),
            "parts_acquisition_cost":   round(parts_acquisition_cost, 0),
            "total_acquisition_cost":   round(battery_acquisition_cost + parts_acquisition_cost, 0),
            "batteries_added":          len(batteries_in_period),
            "battery_by_model":  {k: round(v, 0) for k, v in sorted(battery_by_model.items(), key=lambda x: -x[1])},
            "parts_by_category": {k: round(v, 0) for k, v in sorted(parts_by_cat.items(), key=lambda x: -x[1])},
        },

        # ── Tổng hợp ─────────────────────────────────────────────
        "summary": {
            "total_income":   round(total_income, 0),
            "total_refunds":  round(completed_refunds, 0),
            "total_expense":  round(total_expense, 0),
            "net_cashflow":   round(net_cashflow, 0),
            "profit_margin_pct": round(
                (net_cashflow / total_income * 100) if total_income > 0 else 0, 1
            ),
        },

        # ── Lịch sử ──────────────────────────────────────────────
        "transaction_history": history,
    }
