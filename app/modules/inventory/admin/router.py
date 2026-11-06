"""
[Admin] Inventory — Quản lý kho linh kiện, duyệt đề xuất, báo cáo tài chính

Endpoints:
  GET  /admin/inventory                     — toàn bộ kho theo hub
  GET  /admin/inventory/categories          — danh mục linh kiện
  POST /admin/inventory/categories          — tạo danh mục mới
  PATCH /admin/inventory/categories/{id}    — cập nhật danh mục
  POST /admin/inventory/receive             — nhập kho (Admin thêm linh kiện)
  GET  /admin/inventory/transactions        — log giao dịch kho
  GET  /admin/inventory/parts-requests      — tất cả đề xuất chờ duyệt
  PATCH /admin/inventory/parts-requests/{id}/approve — duyệt đề xuất
  PATCH /admin/inventory/parts-requests/{id}/reject  — từ chối đề xuất
  POST /admin/inventory/transfer            — điều phối linh kiện giữa các hub
  GET  /admin/inventory/finance-report      — báo cáo thu/chi
"""

from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import get_current_user, CurrentUser
from app.shared.roles import UserRole
from app.shared.dependencies import RoleChecker
from app.modules.inventory.models import (
    PartCategory, PartCategoryType, HubInventory,
    InventoryTransaction, PartsRequest,
    PartsRequestStatus, TxnReason,
)
from app.modules.missions.models import Mission, MissionStatus
from app.modules.system.models import Hub

router = APIRouter(prefix="/admin/inventory", tags=["Admin - Inventory"])
allow_admin = RoleChecker([UserRole.ADMIN])

COMPLETED_MS = (MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED)


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


class ReceiveStockRequest(BaseModel):
    hub_id: int
    part_category_id: int
    quantity: int
    unit_cost_override: Optional[float] = None  # ghi đè giá nếu cần
    note: Optional[str] = None


class TransferRequest(BaseModel):
    from_hub_id: int
    to_hub_id: int
    part_category_id: int
    quantity: int
    note: Optional[str] = None


class ReviewRequest(BaseModel):
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


# ── Categories ─────────────────────────────────────────────────────

@router.get("/categories", summary="Danh mục tất cả loại linh kiện")
async def list_categories(
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    rows = (await db.execute(
        select(PartCategory).order_by(PartCategory.category_type, PartCategory.name)
    )).scalars().all()
    return rows


@router.post("/categories", summary="Tạo danh mục linh kiện mới")
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    # Check duplicate name
    existing = (await db.execute(
        select(PartCategory).where(PartCategory.name == body.name)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Danh mục '{body.name}' đã tồn tại.")

    cat = PartCategory(
        name=body.name,
        category_type=body.category_type,
        compatible_drone_models=body.compatible_drone_models,
        unit_cost=body.unit_cost,
        description=body.description,
        is_active=True,
        created_at=datetime.now(timezone.utc),
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
    if body.name is not None:
        cat.name = body.name
    if body.unit_cost is not None:
        cat.unit_cost = body.unit_cost
    if body.description is not None:
        cat.description = body.description
    if body.compatible_drone_models is not None:
        cat.compatible_drone_models = body.compatible_drone_models
    if body.is_active is not None:
        cat.is_active = body.is_active
    await db.commit()
    await db.refresh(cat)
    return cat


# ── Inventory ──────────────────────────────────────────────────────

@router.get("", summary="Tổng quan kho linh kiện toàn hệ thống")
async def get_all_inventory(
    hub_id: Optional[int] = Query(None),
    low_stock_only: bool = Query(False),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = (
        select(HubInventory, PartCategory, Hub)
        .join(PartCategory, HubInventory.part_category_id == PartCategory.id)
        .join(Hub, HubInventory.hub_id == Hub.id)
        .order_by(Hub.name, PartCategory.category_type, PartCategory.name)
    )
    if hub_id:
        q = q.where(HubInventory.hub_id == hub_id)
    if low_stock_only:
        q = q.where(HubInventory.quantity <= HubInventory.min_stock_threshold)

    rows = (await db.execute(q)).all()

    result = []
    for inv, cat, hub in rows:
        result.append({
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
        })
    return result


@router.post("/receive", summary="Nhập kho linh kiện (Admin cấp phát)")
async def receive_stock(
    body: ReceiveStockRequest,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Admin nhập linh kiện vào kho Hub.
    Tạo/cập nhật HubInventory + ghi InventoryTransaction.
    """
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Số lượng nhập tối thiểu là 1.")

    cat = await db.get(PartCategory, body.part_category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Danh mục linh kiện không tồn tại.")

    hub = await db.get(Hub, body.hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub không tồn tại.")

    now = datetime.now(timezone.utc)

    # Upsert HubInventory
    inv = (await db.execute(
        select(HubInventory).where(
            and_(HubInventory.hub_id == body.hub_id,
                 HubInventory.part_category_id == body.part_category_id)
        )
    )).scalar_one_or_none()

    if inv:
        inv.quantity += body.quantity
        inv.updated_at = now
    else:
        inv = HubInventory(
            hub_id=body.hub_id,
            part_category_id=body.part_category_id,
            quantity=body.quantity,
            min_stock_threshold=2,
            updated_at=now,
        )
        db.add(inv)

    # Ghi transaction
    unit_cost = body.unit_cost_override or cat.unit_cost
    total_cost = (unit_cost or 0) * body.quantity
    db.add(InventoryTransaction(
        hub_id=body.hub_id,
        part_category_id=body.part_category_id,
        quantity_delta=body.quantity,
        reason=TxnReason.RECEIVE,
        performed_by=user.id,
        note=f"Nhập kho {body.quantity}x {cat.name} tại hub #{body.hub_id}. "
             f"Đơn giá: {unit_cost:,.0f} VNĐ. Tổng: {total_cost:,.0f} VNĐ. "
             + (body.note or ""),
        created_at=now,
    ))

    await db.commit()
    return {
        "message": f"Nhập kho thành công {body.quantity}x {cat.name} tại hub #{body.hub_id}.",
        "new_quantity": inv.quantity,
        "total_cost": total_cost,
    }


@router.post("/transfer", summary="Điều phối linh kiện giữa các Hub")
async def transfer_stock(
    body: TransferRequest,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Admin chuyển linh kiện từ hub A sang hub B.
    Sinh 2 transaction: -qty ở nguồn, +qty ở đích.
    """
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Số lượng chuyển tối thiểu là 1.")
    if body.from_hub_id == body.to_hub_id:
        raise HTTPException(status_code=400, detail="Hub nguồn và đích phải khác nhau.")

    now = datetime.now(timezone.utc)

    # Kiểm tra tồn kho nguồn
    src_inv = (await db.execute(
        select(HubInventory).where(
            and_(HubInventory.hub_id == body.from_hub_id,
                 HubInventory.part_category_id == body.part_category_id)
        )
    )).scalar_one_or_none()

    if not src_inv or src_inv.quantity < body.quantity:
        avail = src_inv.quantity if src_inv else 0
        raise HTTPException(
            status_code=400,
            detail=f"Hub nguồn #{body.from_hub_id} chỉ có {avail} đơn vị, không đủ để chuyển {body.quantity}."
        )

    cat = await db.get(PartCategory, body.part_category_id)
    cat_name = cat.name if cat else f"#part_{body.part_category_id}"

    # Trừ kho nguồn
    src_inv.quantity -= body.quantity
    src_inv.updated_at = now

    # Cộng kho đích
    dst_inv = (await db.execute(
        select(HubInventory).where(
            and_(HubInventory.hub_id == body.to_hub_id,
                 HubInventory.part_category_id == body.part_category_id)
        )
    )).scalar_one_or_none()

    if dst_inv:
        dst_inv.quantity += body.quantity
        dst_inv.updated_at = now
    else:
        dst_inv = HubInventory(
            hub_id=body.to_hub_id,
            part_category_id=body.part_category_id,
            quantity=body.quantity,
            min_stock_threshold=2,
            updated_at=now,
        )
        db.add(dst_inv)

    ref = f"TRANSFER_{body.from_hub_id}_to_{body.to_hub_id}_{int(now.timestamp())}"
    note_base = body.note or ""
    db.add(InventoryTransaction(
        hub_id=body.from_hub_id, part_category_id=body.part_category_id,
        quantity_delta=-body.quantity, reason=TxnReason.TRANSFER_OUT,
        performed_by=user.id, reference_id=ref,
        note=f"Chuyển {body.quantity}x {cat_name} → hub #{body.to_hub_id}. {note_base}",
        created_at=now,
    ))
    db.add(InventoryTransaction(
        hub_id=body.to_hub_id, part_category_id=body.part_category_id,
        quantity_delta=body.quantity, reason=TxnReason.TRANSFER_IN,
        performed_by=user.id, reference_id=ref,
        note=f"Nhận {body.quantity}x {cat_name} từ hub #{body.from_hub_id}. {note_base}",
        created_at=now,
    ))

    await db.commit()
    return {
        "message": f"Điều phối thành công {body.quantity}x {cat_name} từ hub #{body.from_hub_id} → hub #{body.to_hub_id}.",
        "from_hub_new_qty": src_inv.quantity,
        "to_hub_new_qty": dst_inv.quantity,
    }


# ── Transactions log ───────────────────────────────────────────────

@router.get("/transactions", response_model=List[TxnOut],
            summary="Log giao dịch kho toàn hệ thống")
async def list_transactions(
    hub_id: Optional[int] = Query(None),
    part_category_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = select(InventoryTransaction).order_by(InventoryTransaction.created_at.desc()).limit(limit)
    if hub_id:
        q = q.where(InventoryTransaction.hub_id == hub_id)
    if part_category_id:
        q = q.where(InventoryTransaction.part_category_id == part_category_id)

    rows = (await db.execute(q)).scalars().all()

    # Lấy part names
    cat_ids = list({r.part_category_id for r in rows})
    cats = {}
    if cat_ids:
        cat_rows = (await db.execute(
            select(PartCategory).where(PartCategory.id.in_(cat_ids))
        )).scalars().all()
        cats = {c.id: c.name for c in cat_rows}

    result = []
    for r in rows:
        out = TxnOut.model_validate(r)
        out.part_name = cats.get(r.part_category_id)
        result.append(out)
    return result


# ── Parts Requests ─────────────────────────────────────────────────

@router.get("/parts-requests", response_model=List[PartsRequestOut],
            summary="Danh sách đề xuất linh kiện — chờ duyệt và toàn bộ")
async def list_all_requests(
    status: Optional[str] = Query(None, description="Lọc theo status (Pending, Approved, ...)"),
    hub_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    q = select(PartsRequest).order_by(PartsRequest.created_at.desc()).limit(limit)
    if status:
        q = q.where(PartsRequest.status == status)
    if hub_id:
        q = q.where(PartsRequest.hub_id == hub_id)

    rows = (await db.execute(q)).scalars().all()

    cat_ids = list({r.part_category_id for r in rows})
    cats, inv_map = {}, {}
    if cat_ids:
        cat_rows = (await db.execute(
            select(PartCategory).where(PartCategory.id.in_(cat_ids))
        )).scalars().all()
        cats = {c.id: c.name for c in cat_rows}

    # Lấy tồn kho hiện tại cho từng (hub, part)
    if rows:
        hub_part_pairs = {(r.hub_id, r.part_category_id) for r in rows}
        hub_ids_q = list({p[0] for p in hub_part_pairs})
        part_ids_q = list({p[1] for p in hub_part_pairs})
        inv_rows = (await db.execute(
            select(HubInventory).where(
                and_(HubInventory.hub_id.in_(hub_ids_q),
                     HubInventory.part_category_id.in_(part_ids_q))
            )
        )).scalars().all()
        inv_map = {(i.hub_id, i.part_category_id): i.quantity for i in inv_rows}

    result = []
    for r in rows:
        out = PartsRequestOut.model_validate(r)
        out.part_name = cats.get(r.part_category_id)
        out.hub_stock_available = inv_map.get((r.hub_id, r.part_category_id))
        result.append(out)
    return result


@router.patch("/parts-requests/{request_id}/approve",
              summary="Duyệt đề xuất thay linh kiện")
async def approve_request(
    request_id: int,
    body: ReviewRequest,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    Admin duyệt đề xuất:
    - Nếu hub có đủ tồn kho → trừ kho ngay, status → APPROVED
    - Nếu không đủ → status → WAITING_STOCK (admin cần transfer trước)
    """
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
    if req.status not in (PartsRequestStatus.PENDING, PartsRequestStatus.WAITING_STOCK):
        raise HTTPException(
            status_code=400,
            detail=f"Đề xuất đang ở trạng thái '{req.status.value}', không thể duyệt."
        )

    now = datetime.now(timezone.utc)

    # Kiểm tra tồn kho
    inv = (await db.execute(
        select(HubInventory).where(
            and_(HubInventory.hub_id == req.hub_id,
                 HubInventory.part_category_id == req.part_category_id)
        )
    )).scalar_one_or_none()

    has_stock = inv is not None and inv.quantity >= req.quantity_needed

    req.reviewed_by = user.id
    req.reviewed_at = now
    req.admin_note = body.admin_note

    if has_stock:
        req.status = PartsRequestStatus.APPROVED
        inv.quantity -= req.quantity_needed
        inv.updated_at = now

        cat = await db.get(PartCategory, req.part_category_id)
        db.add(InventoryTransaction(
            hub_id=req.hub_id,
            part_category_id=req.part_category_id,
            quantity_delta=-req.quantity_needed,
            reason=TxnReason.REPAIR_USE,
            performed_by=user.id,
            reference_id=str(req.id),
            note=f"Admin duyệt đề xuất #{req.id}: {req.quantity_needed}x {cat.name if cat else '?'} cho drone {req.drone_id or 'N/A'}",
            created_at=now,
        ))

        await db.commit()
        return {
            "message": "Đã duyệt. Linh kiện đã được trừ khỏi kho.",
            "request_id": request_id,
            "status": PartsRequestStatus.APPROVED.value,
            "remaining_stock": inv.quantity,
        }
    else:
        req.status = PartsRequestStatus.WAITING_STOCK
        await db.commit()
        avail = inv.quantity if inv else 0
        return {
            "message": f"Chuyển sang WAITING_STOCK. Hub #{req.hub_id} chỉ còn {avail} đơn vị (cần {req.quantity_needed}). Hãy điều phối từ hub khác.",
            "request_id": request_id,
            "status": PartsRequestStatus.WAITING_STOCK.value,
            "hub_stock_available": avail,
        }


@router.patch("/parts-requests/{request_id}/reject",
              summary="Từ chối đề xuất thay linh kiện")
async def reject_request(
    request_id: int,
    body: ReviewRequest,
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    req = await db.get(PartsRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Đề xuất không tồn tại.")
    if req.status in (PartsRequestStatus.FULFILLED, PartsRequestStatus.REJECTED):
        raise HTTPException(status_code=400, detail="Đề xuất đã kết thúc.")

    now = datetime.now(timezone.utc)
    req.status = PartsRequestStatus.REJECTED
    req.reviewed_by = user.id
    req.reviewed_at = now
    req.admin_note = body.admin_note or "Từ chối bởi Admin."

    await db.commit()
    return {"message": "Đã từ chối đề xuất.", "request_id": request_id}


# ── Finance Report ─────────────────────────────────────────────────

@router.get("/finance-report", summary="Báo cáo thu/chi linh kiện & doanh thu")
async def finance_report(
    month: Optional[int] = Query(None, ge=1, le=12, description="Tháng (1-12), để trống = tất cả"),
    year: Optional[int] = Query(None, ge=2020, description="Năm, để trống = năm hiện tại"),
    db: AsyncSession = Depends(get_async_db),
    user: CurrentUser = Depends(allow_admin),
):
    """
    **Báo cáo dòng tiền hệ thống.**

    Thu:
    - Doanh thu từ phí giao hàng (missions đã hoàn tất)

    Chi:
    - Chi phí linh kiện: `actual_cost` từ parts_requests FULFILLED
    - Chi phí nhập kho:  `unit_cost * qty` từ inventory_transactions RECEIVE

    Không bao gồm lương nhân công.
    """
    from sqlalchemy.sql.functions import coalesce

    now = datetime.now(timezone.utc)
    target_year = year or now.year

    # Xác định khoảng thời gian
    if month:
        from calendar import monthrange
        _, last_day = monthrange(target_year, month)
        period_start = datetime(target_year, month, 1, tzinfo=timezone.utc)
        period_end   = datetime(target_year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        period_label = f"Tháng {month:02d}/{target_year}"
    else:
        period_start = datetime(target_year, 1, 1, tzinfo=timezone.utc)
        period_end   = datetime(target_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        period_label = f"Năm {target_year}"

    # ── Thu: doanh thu giao hàng ──────────────────────────────────
    period_start_naive = period_start.replace(tzinfo=None)
    period_end_naive = period_end.replace(tzinfo=None)
    comp_ts = func.coalesce(Mission.arrival_confirmed_at, Mission.end_time, Mission.departed_at)
    revenue_q = await db.execute(
        select(func.coalesce(func.sum(Mission.delivery_fee), 0.0))
        .where(
            and_(
                Mission.status.in_(COMPLETED_MS),
                comp_ts >= period_start_naive,
                comp_ts <= period_end_naive,
            )
        )
    )
    total_revenue = float(revenue_q.scalar() or 0.0)

    # ── Chi: chi phí linh kiện từ parts_requests FULFILLED ────────
    fulfilled_cost_q = await db.execute(
        select(func.coalesce(func.sum(PartsRequest.actual_cost), 0.0))
        .where(
            and_(
                PartsRequest.status == PartsRequestStatus.FULFILLED,
                PartsRequest.fulfilled_at >= period_start,
                PartsRequest.fulfilled_at <= period_end,
                PartsRequest.actual_cost.isnot(None),
            )
        )
    )
    repair_cost = float(fulfilled_cost_q.scalar() or 0.0)

    # ── Chi: chi phí nhập kho từ transactions ─────────────────────
    # Ước tính: qty * unit_cost của category (không lưu cost vào txn)
    # Tính qua JOIN PartCategory
    receive_txns = (await db.execute(
        select(InventoryTransaction, PartCategory)
        .join(PartCategory, InventoryTransaction.part_category_id == PartCategory.id)
        .where(
            and_(
                InventoryTransaction.reason == TxnReason.RECEIVE,
                InventoryTransaction.created_at >= period_start,
                InventoryTransaction.created_at <= period_end,
            )
        )
    )).all()

    receive_cost = sum(
        (cat.unit_cost or 0) * txn.quantity_delta
        for txn, cat in receive_txns
    )

    total_expense = repair_cost + receive_cost
    net = total_revenue - total_expense

    # ── Chi tiết theo category ────────────────────────────────────
    cat_expense: dict[str, float] = {}
    for txn, cat in receive_txns:
        cost = (cat.unit_cost or 0) * txn.quantity_delta
        cat_expense[cat.name] = cat_expense.get(cat.name, 0) + cost

    fulfilled_reqs = (await db.execute(
        select(PartsRequest, PartCategory)
        .join(PartCategory, PartsRequest.part_category_id == PartCategory.id)
        .where(
            and_(
                PartsRequest.status == PartsRequestStatus.FULFILLED,
                PartsRequest.fulfilled_at >= period_start,
                PartsRequest.fulfilled_at <= period_end,
                PartsRequest.actual_cost.isnot(None),
            )
        )
    )).all()

    for req, cat in fulfilled_reqs:
        cat_expense[cat.name] = cat_expense.get(cat.name, 0) + (req.actual_cost or 0)

    # ── Thống kê đề xuất trong kỳ ────────────────────────────────
    total_requests = (await db.execute(
        select(func.count()).select_from(PartsRequest).where(
            and_(PartsRequest.created_at >= period_start,
                 PartsRequest.created_at <= period_end)
        )
    )).scalar() or 0

    fulfilled_count = (await db.execute(
        select(func.count()).select_from(PartsRequest).where(
            and_(PartsRequest.status == PartsRequestStatus.FULFILLED,
                 PartsRequest.fulfilled_at >= period_start,
                 PartsRequest.fulfilled_at <= period_end)
        )
    )).scalar() or 0

    return {
        "period": period_label,
        "revenue": {
            "delivery_fees": round(total_revenue, 2),
            "total_income": round(total_revenue, 2),
        },
        "expense": {
            "parts_procurement": round(receive_cost, 2),
            "parts_repair_use": round(repair_cost, 2),
            "total_expense": round(total_expense, 2),
            "by_category": {k: round(v, 2) for k, v in sorted(cat_expense.items(), key=lambda x: -x[1])},
        },
        "net_cashflow": round(net, 2),
        "summary": {
            "total_parts_requests": int(total_requests),
            "fulfilled_requests": int(fulfilled_count),
            "profit_margin_pct": round(net / total_revenue * 100, 1) if total_revenue > 0 else None,
        },
    }
