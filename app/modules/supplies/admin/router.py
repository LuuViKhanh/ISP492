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

router = APIRouter(prefix="/admin/supplies", tags=["Admin - Supplies"])
allow_admin = RoleChecker([UserRole.ADMIN])

_COMPLETED_MS = (MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED)


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
    total_items = (await db.execute(
        select(func.coalesce(func.sum(HubSupply.quantity), 0))
    )).scalar() or 0

    low_stock = (await db.execute(
        select(func.count()).select_from(HubSupply)
        .where(HubSupply.quantity <= HubSupply.min_stock_threshold)
    )).scalar() or 0

    pending_requests = (await db.execute(
        select(func.count()).select_from(PartsRequest)
        .where(PartsRequest.status == PartsRequestStatus.PENDING)
    )).scalar() or 0

    total_categories = (await db.execute(
        select(func.count()).select_from(PartCategory).where(PartCategory.is_active == True)
    )).scalar() or 0

    return {
        "total_items_in_stock": int(total_items),
        "low_stock_alerts": int(low_stock),
        "pending_requests": int(pending_requests),
        "total_categories": int(total_categories),
    }


# ── Categories ─────────────────────────────────────────────────────

@router.get("/categories", summary="Danh mục tất cả loại linh kiện / vật tư")
async def list_categories(
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    rows = (await db.execute(
        select(PartCategory).order_by(PartCategory.category_type, PartCategory.name)
    )).scalars().all()
    return rows


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
        raise HTTPException(status_code=409, detail=f"Danh mục '{body.name}' đã tồn tại.")
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
        raise HTTPException(status_code=404, detail="Danh mục không tồn tại.")
    if body.name is not None: cat.name = body.name
    if body.unit_cost is not None: cat.unit_cost = body.unit_cost
    if body.description is not None: cat.description = body.description
    if body.compatible_drone_models is not None: cat.compatible_drone_models = body.compatible_drone_models
    if body.is_active is not None: cat.is_active = body.is_active
    await db.commit()
    await db.refresh(cat)
    return cat


# ── Supplies (stock per hub) ────────────────────────────────────────

@router.get("", summary="Số lượng vật tư theo Hub toàn hệ thống")
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
    if hub_id:
        q = q.where(HubSupply.hub_id == hub_id)
    if low_stock_only:
        q = q.where(HubSupply.quantity <= HubSupply.min_stock_threshold)
    if category_type:
        q = q.where(PartCategory.category_type == category_type)

    rows = (await db.execute(q)).all()
    return [
        {
            "id": inv.id,
            "hub_id": hub.id,
            "hub_name": hub.name,
            "part_category_id": cat.id,
            "part_name": cat.name,
            "category_type": cat.category_type.value if hasattr(cat.category_type, "value") else str(cat.category_type),
            "compatible_drone_models": cat.compatible_drone_models,
            "unit_cost": cat.unit_cost,
            "quantity": inv.quantity,
            "min_stock_threshold": inv.min_stock_threshold,
            "is_low_stock": inv.quantity <= inv.min_stock_threshold,
            "updated_at": inv.updated_at.isoformat() if inv.updated_at else None,
        }
        for inv, cat, hub in rows
    ]


@router.post("/receive", summary="Nhập vật tư từ nhà cung cấp vào Hub")
async def receive_stock(
    body: ReceiveStockBody,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Số lượng nhập tối thiểu là 1.")
    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Danh mục linh kiện không tồn tại.")
    hub = await db.get(Hub, body.hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub không tồn tại.")

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
        note=f"Nhập {body.quantity}x {cat.name} tại hub #{body.hub_id}. "
             f"Đơn giá: {unit_cost:,.0f} VNĐ. Tổng: {total_cost:,.0f} VNĐ. "
             + (body.note or ""),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Nhập thành công {body.quantity}x {cat.name} → hub #{body.hub_id}.",
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
        raise HTTPException(status_code=400, detail="Số lượng chuyển tối thiểu là 1.")
    if body.from_hub_id == body.to_hub_id:
        raise HTTPException(status_code=400, detail="Hub nguồn và đích phải khác nhau.")

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
            detail=f"Hub #{body.from_hub_id} chỉ có {avail} đơn vị, không đủ {body.quantity}.",
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
        note=f"Điều phối {body.quantity}x {cat_name} → hub #{body.to_hub_id}. {note_base}",
        created_at=now,
    ))
    db.add(SupplyTransaction(
        hub_id=body.to_hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity, reason=SupplyTxnReason.TRANSFER_IN,
        performed_by=user.id, reference_id=ref,
        note=f"Nhận điều phối {body.quantity}x {cat_name} từ hub #{body.from_hub_id}. {note_base}",
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Điều phối thành công {body.quantity}x {cat_name}: hub #{body.from_hub_id} → hub #{body.to_hub_id}.",
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
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
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
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
    if req.status not in (PartsRequestStatus.PENDING, PartsRequestStatus.WAITING_STOCK):
        raise HTTPException(status_code=400,
            detail=f"Trạng thái '{req.status.value}' không thể duyệt.")

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
            note=f"Admin duyệt đề xuất #{req.id}: {req.quantity_needed}x "
                 f"{cat.name if cat else '?'} cho drone {req.drone_id or 'N/A'}",
            created_at=now,
        ))
        await db.commit()
        return {"message": "Đã duyệt, linh kiện đã được trừ tại Hub.",
                "request_id": request_id, "status": PartsRequestStatus.APPROVED.value,
                "remaining_stock": inv.quantity}
    else:
        req.status = PartsRequestStatus.WAITING_STOCK
        await db.commit()
        avail = inv.quantity if inv else 0
        return {"message": f"WAITING_STOCK — Hub #{req.hub_id} chỉ còn {avail} "
                           f"(cần {req.quantity_needed}). Hãy điều phối từ Hub khác.",
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
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
    if req.status in (PartsRequestStatus.FULFILLED, PartsRequestStatus.REJECTED):
        raise HTTPException(status_code=400, detail="Đề xuất đã kết thúc, không thể từ chối.")
    now = datetime.now(timezone.utc)
    req.status = PartsRequestStatus.REJECTED
    req.reviewed_by = user.id
    req.reviewed_at = now
    req.admin_note = body.admin_note or "Từ chối bởi Admin."
    await db.commit()
    return {"message": "Đã từ chối đề xuất.", "request_id": request_id}


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
        label   = f"Tháng {month:02d}/{target_year}"
    else:
        p_start = datetime(target_year, 1, 1, tzinfo=timezone.utc)
        p_end   = datetime(target_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        label   = f"Năm {target_year}"

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

    # Chi: sửa chữa (parts fulfilled)
    repair_cost = float((await db.execute(
        select(func.coalesce(func.sum(PartsRequest.actual_cost), 0.0))
        .where(and_(
            PartsRequest.status == PartsRequestStatus.FULFILLED,
            PartsRequest.fulfilled_at >= p_start,
            PartsRequest.fulfilled_at <= p_end,
            PartsRequest.actual_cost.isnot(None),
        ))
    )).scalar() or 0.0)

    # Chi: nhập kho từ nhà cung cấp
    receive_txns = (await db.execute(
        select(SupplyTransaction, PartCategory)
        .join(PartCategory, SupplyTransaction.part_category_id == PartCategory.id)
        .where(and_(
            SupplyTransaction.reason == SupplyTxnReason.RECEIVE,
            SupplyTransaction.created_at >= p_start,
            SupplyTransaction.created_at <= p_end,
        ))
    )).all()
    receive_cost = sum((cat.unit_cost or 0) * txn.quantity_delta for txn, cat in receive_txns)

    # Chi tiết theo category
    cat_expense: dict[str, float] = {}
    for txn, cat in receive_txns:
        cat_expense[cat.name] = cat_expense.get(cat.name, 0) + (cat.unit_cost or 0) * txn.quantity_delta

    fulfilled_reqs = (await db.execute(
        select(PartsRequest, PartCategory)
        .join(PartCategory, PartsRequest.part_category_id == PartCategory.id)
        .where(and_(
            PartsRequest.status == PartsRequestStatus.FULFILLED,
            PartsRequest.fulfilled_at >= p_start,
            PartsRequest.fulfilled_at <= p_end,
            PartsRequest.actual_cost.isnot(None),
        ))
    )).all()
    for req, cat in fulfilled_reqs:
        cat_expense[cat.name] = cat_expense.get(cat.name, 0) + (req.actual_cost or 0)

    total_expense = repair_cost + receive_cost
    net = total_revenue - total_expense

    total_requests = int((await db.execute(
        select(func.count()).select_from(PartsRequest).where(and_(
            PartsRequest.created_at >= p_start, PartsRequest.created_at <= p_end,
        ))
    )).scalar() or 0)
    fulfilled_count = int((await db.execute(
        select(func.count()).select_from(PartsRequest).where(and_(
            PartsRequest.status == PartsRequestStatus.FULFILLED,
            PartsRequest.fulfilled_at >= p_start, PartsRequest.fulfilled_at <= p_end,
        ))
    )).scalar() or 0)

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
        raise HTTPException(status_code=404, detail="Hub không tồn tại.")

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
        raise HTTPException(status_code=404, detail="Hub không tồn tại.")
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
            raise HTTPException(status_code=400, detail="Hub chưa có mục linh kiện này, không thể trừ.")
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
                detail=f"Không đủ số lượng. Hiện có {inv.quantity}, điều chỉnh -{abs(body.quantity_delta)} = âm.",
            )
        inv.quantity = new_qty
        inv.updated_at = now

    db.add(SupplyTransaction(
        hub_id=hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity_delta, reason=body.reason,
        performed_by=user.id,
        note=f"[Admin] Điều chỉnh kiểm kê {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name}. "
             + (body.note or ""),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Đã điều chỉnh {'+' if body.quantity_delta > 0 else ''}{body.quantity_delta}x {cat.name} tại hub #{hub_id}.",
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
        raise HTTPException(status_code=400, detail="Số lượng hủy tối thiểu là 1.")

    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub không tồn tại.")
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
        note=f"[Admin] Hủy {body.quantity}x {cat.name} tại hub #{hub_id}. "
             + (body.note or "Linh kiện hỏng / hết hạn."),
        created_at=now,
    ))
    await db.commit()
    return {
        "message": f"Đã hủy {body.quantity}x {cat.name} tại hub #{hub_id}.",
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
        raise HTTPException(status_code=400, detail="Ngưỡng tối thiểu phải >= 0.")

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
        "message": f"Đã cập nhật ngưỡng cảnh báo = {body.min_stock_threshold}.",
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
        raise HTTPException(status_code=400, detail="Danh sách mặt hàng không được rỗng.")

    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub không tồn tại.")

    now = datetime.now(timezone.utc)
    results = []
    total_cost = 0.0

    for item in body.items:
        if item.quantity < 1:
            raise HTTPException(status_code=400, detail=f"Số lượng cho part #{item.part_category_id} phải >= 1.")
        cat = await db.get(PartCategory, item.part_category_id)
        if not cat:
            raise HTTPException(status_code=404, detail=f"Danh mục #{item.part_category_id} không tồn tại.")

        inv = (await db.execute(
            select(HubSupply).where(and_(
                HubSupply.hub_id == hub_id,
                HubSupply.part_category_id == item.part_category_id,
            ))
        )).scalar_one_or_none()

        if inv:
            inv.quantity += item.quantity
            inv.updated_at = now
        else:
            inv = HubSupply(
                hub_id=hub_id, part_category_id=item.part_category_id,
                quantity=item.quantity, min_stock_threshold=2, updated_at=now,
            )
            db.add(inv)

        unit_cost = item.unit_cost_override or cat.unit_cost or 0
        line_cost = unit_cost * item.quantity
        total_cost += line_cost

        db.add(SupplyTransaction(
            hub_id=hub_id, part_category_id=item.part_category_id,
            quantity_delta=item.quantity, reason=SupplyTxnReason.RECEIVE,
            performed_by=user.id,
            note=f"[Bulk] Nhập {item.quantity}x {cat.name}. Đơn giá: {unit_cost:,.0f} VNĐ. "
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
        "message": f"Nhập thành công {len(results)} loại linh kiện vào hub #{hub_id}.",
        "hub_id": hub_id,
        "hub_name": hub.name,
        "items": results,
        "total_cost": round(total_cost, 0),
    }
