from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import Drone, WorkOrder, WorkOrderStatus
from app.modules.fleet.schemas import WorkOrderCreate, WorkOrderUpdate, InspectionUpdate, WorkOrderResponse
from app.modules.fleet.technician.schemas import DroneProfileResponse, DroneStatusUpdate, MaintenanceHistoryItem

router = APIRouter(prefix="/technician/fleet", tags=["Technician - Fleet"])

allow_technician = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])


# ── Drone Profile ─────────────────────────────────────────────────────────────

@router.get("/drones", response_model=list[DroneProfileResponse])
async def list_drones(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(Drone).order_by(Drone.id))
    return result.scalars().all()


@router.get("/drones/{drone_id}", response_model=DroneProfileResponse)
async def get_drone_profile(
    drone_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    return drone


@router.patch("/drones/{drone_id}/status", response_model=DroneProfileResponse)
async def update_drone_status(
    drone_id: int,
    body: DroneStatusUpdate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    drone.status = body.status
    await db.commit()
    await db.refresh(drone)
    return drone


@router.get("/drones/{drone_id}/maintenance-history", response_model=list[MaintenanceHistoryItem])
async def get_drone_maintenance_history(
    drone_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(WorkOrder).where(WorkOrder.drone_id == drone_id).order_by(WorkOrder.id.desc())
    )
    return result.scalars().all()


# ── Work Order CRUD ───────────────────────────────────────────────────────────

@router.get("/work-orders", response_model=list[WorkOrderResponse])
async def list_work_orders(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(WorkOrder).order_by(WorkOrder.id.desc()))
    return result.scalars().all()


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderResponse)
async def get_work_order(
    work_order_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    return wo


@router.post("/work-orders", response_model=WorkOrderResponse, status_code=201)
async def create_work_order(
    body: WorkOrderCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = WorkOrder(
        drone_id=body.drone_id,
        battery_id=body.battery_id,
        technician_id=int(user.id) if user.id.isdigit() else 0,
        issue_description=body.issue_description,
        status=WorkOrderStatus.PENDING,
    )
    db.add(wo)
    await db.commit()
    await db.refresh(wo)
    return wo


@router.put("/work-orders/{work_order_id}", response_model=WorkOrderResponse)
async def update_work_order(
    work_order_id: int,
    body: WorkOrderUpdate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    if body.issue_description is not None:
        wo.issue_description = body.issue_description
    if body.action_taken is not None:
        wo.action_taken = body.action_taken
    await db.commit()
    await db.refresh(wo)
    return wo


@router.delete("/work-orders/{work_order_id}", status_code=204)
async def delete_work_order(
    work_order_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    await db.delete(wo)
    await db.commit()


# ── Inspection & Update Status ────────────────────────────────────────────────

@router.patch("/work-orders/{work_order_id}/inspection", response_model=WorkOrderResponse)
async def update_inspection(
    work_order_id: int,
    body: InspectionUpdate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    wo.action_taken = body.action_taken
    wo.status = body.status
    if body.status == WorkOrderStatus.COMPLETED:
        wo.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(wo)
    return wo
