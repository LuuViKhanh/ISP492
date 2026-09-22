from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import (
    Drone, WorkOrder, WorkOrderStatus,
    MaintenanceRecord, MaintenanceInspectionItem, WorkOrderLog, MaintenanceAlert, MaintenanceSchedule
)
from app.modules.fleet.schemas import WorkOrderCreate, WorkOrderUpdate, InspectionUpdate, WorkOrderResponse
from app.modules.fleet.technician.schemas import (
    DroneProfileResponse, DroneStatusUpdate, MaintenanceHistoryItem,
    MaintenanceRecordCreate, MaintenanceRecordResponse,
    WorkOrderLogResponse, MaintenanceAlertResponse,
    MaintenanceScheduleResponse, MaintenanceScheduleCreate
)

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


# ── Maintenance Records ───────────────────────────────────────────────────────

@router.get("/maintenance-records/{drone_id}", response_model=list[MaintenanceRecordResponse])
async def get_maintenance_records(
    drone_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(MaintenanceRecord).where(MaintenanceRecord.drone_id == drone_id).order_by(MaintenanceRecord.id.desc())
    )
    records = result.scalars().all()
    response = []
    for record in records:
        items_result = await db.execute(
            select(MaintenanceInspectionItem).where(MaintenanceInspectionItem.maintenance_record_id == record.id)
        )
        items = items_result.scalars().all()
        response.append(MaintenanceRecordResponse(
            id=record.id,
            work_order_id=record.work_order_id,
            drone_id=record.drone_id,
            performed_by=record.performed_by,
            title=record.title,
            diagnosis=record.diagnosis,
            corrective_action=record.corrective_action,
            resulting_drone_status=record.resulting_drone_status,
            created_at=record.created_at,
            completed_at=record.completed_at,
            inspection_items=items,
        ))
    return response


@router.post("/maintenance-records", response_model=MaintenanceRecordResponse, status_code=201)
async def create_maintenance_record(
    body: MaintenanceRecordCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    record = MaintenanceRecord(
        work_order_id=body.work_order_id,
        drone_id=body.drone_id,
        performed_by=user.id,
        title=body.title,
        diagnosis=body.diagnosis,
        corrective_action=body.corrective_action,
        resulting_drone_status=body.resulting_drone_status,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(record)
    await db.flush()

    items = []
    for item in (body.inspection_items or []):
        inspection_item = MaintenanceInspectionItem(
            maintenance_record_id=record.id,
            item_name=item.item_name,
            is_completed=item.is_completed,
            checked_at=datetime.now(timezone.utc).replace(tzinfo=None) if item.is_completed else None,
        )
        db.add(inspection_item)
        items.append(inspection_item)

    await db.commit()
    await db.refresh(record)

    return MaintenanceRecordResponse(
        id=record.id,
        work_order_id=record.work_order_id,
        drone_id=record.drone_id,
        performed_by=record.performed_by,
        title=record.title,
        diagnosis=record.diagnosis,
        corrective_action=record.corrective_action,
        resulting_drone_status=record.resulting_drone_status,
        created_at=record.created_at,
        completed_at=record.completed_at,
        inspection_items=items,
    )


# ── Work Order Logs ───────────────────────────────────────────────────────────

@router.get("/work-orders/{work_order_id}/logs", response_model=list[WorkOrderLogResponse])
async def get_work_order_logs(
    work_order_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(WorkOrderLog).where(WorkOrderLog.work_order_id == work_order_id).order_by(WorkOrderLog.id.desc())
    )
    return result.scalars().all()


# ── Maintenance Schedules ─────────────────────────────────────────────────────

@router.get("/maintenance-schedules", response_model=list[MaintenanceScheduleResponse])
async def list_maintenance_schedules(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(MaintenanceSchedule).order_by(MaintenanceSchedule.next_inspection_at))
    return result.scalars().all()


@router.get("/maintenance-schedules/drone/{drone_id}", response_model=list[MaintenanceScheduleResponse])
async def get_drone_maintenance_schedules(
    drone_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(MaintenanceSchedule)
        .where(MaintenanceSchedule.drone_id == drone_id)
        .order_by(MaintenanceSchedule.next_inspection_at)
    )
    return result.scalars().all()


@router.post("/maintenance-schedules", response_model=MaintenanceScheduleResponse, status_code=201)
async def create_maintenance_schedule(
    body: MaintenanceScheduleCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    schedule = MaintenanceSchedule(
        drone_id=body.drone_id,
        maintenance_type=body.maintenance_type,
        interval_days=body.interval_days,
        interval_flight_hours=body.interval_flight_hours,
        status="active",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


# ── Maintenance Alerts ────────────────────────────────────────────────────────

@router.get("/maintenance-alerts", response_model=list[MaintenanceAlertResponse])
async def list_maintenance_alerts(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(MaintenanceAlert).order_by(MaintenanceAlert.id.desc()))
    return result.scalars().all()


@router.get("/maintenance-alerts/{alert_id}", response_model=MaintenanceAlertResponse)
async def get_maintenance_alert(
    alert_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    alert = await db.get(MaintenanceAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


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
    old_status = wo.status.value
    wo.action_taken = body.action_taken
    wo.status = body.status
    if body.status == WorkOrderStatus.COMPLETED:
        wo.resolved_at = datetime.now(timezone.utc)

    # Ghi log thay đổi status
    log = WorkOrderLog(
        work_order_id=wo.id,
        action="inspection_update",
        from_status=old_status,
        to_status=body.status.value,
        changed_by=user.id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(log)

    await db.commit()
    await db.refresh(wo)
    return wo


from app.modules.fleet.technician.service import run_maintenance_alerts_logic, run_battery_overdue_alerts_logic

# ── Background Workers / Cronjobs ─────────────────────────────────────────────

@router.post("/cron/maintenance-alerts", summary="FDE-118: Worker sinh Maintenance Alert")
async def worker_maintenance_alerts(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    inserted_ids = await run_maintenance_alerts_logic(db)
    
    return {
        "message": "Maintenance alerts generated successfully",
        "alerts_created": len(inserted_ids),
        "alert_ids": inserted_ids
    }

@router.post("/cron/battery-overdue-alerts", summary="FDE-119: Worker cảnh báo Pin & Quá hạn")
async def worker_battery_overdue_alerts(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    batt_ids, overdue_ids = await run_battery_overdue_alerts_logic(db)
    
    return {
        "message": "Battery and overdue alerts generated successfully",
        "battery_alerts_created": len(batt_ids),
        "overdue_alerts_created": len(overdue_ids)
    }
