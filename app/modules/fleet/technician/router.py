from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, cast, String

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.auth.models import User
from app.modules.fleet.models import (
    Drone, Battery, WorkOrder, WorkOrderStatus,
    MaintenanceRecord, MaintenanceInspectionItem, WorkOrderLog, MaintenanceAlert, MaintenanceSchedule
)
from app.modules.system.models import Hub
from app.modules.fleet.schemas import WorkOrderCreate, WorkOrderUpdate, InspectionUpdate, WorkOrderResponse
from app.modules.fleet.technician.schemas import (
    DroneProfileResponse, DroneStatusUpdate, MaintenanceHistoryItem,
    MaintenanceRecordCreate, MaintenanceRecordResponse,
    WorkOrderLogResponse, MaintenanceAlertResponse,
    MaintenanceScheduleResponse, MaintenanceScheduleCreate,
    IncomingMissionResponse, BatteryResponse, BatteryUseRequest
)
from app.modules.missions.models import Mission, MissionStatus, HandlingStatus, Incident, IncidentStatus
from app.core.config import settings

router = APIRouter(prefix="/technician/fleet", tags=["Fleet"])

allow_technician = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])


# ── Incoming & Confirm Arrival ───────────────────────────────────────────────

@router.get("/incoming")
async def get_incoming_drones(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy import cast, String
    import traceback
    try:
        query = (
            select(
                Mission.id,
                cast(Mission.status, String).label("status"),
                cast(Mission.handling_status, String).label("handling_status"),
                Drone.name.label("drone_code"),
                Drone.model.label("model"),
                Battery.serial_number.label("battery_code"),
                Battery.charge_level_pct.label("battery_percent")
            )
            .outerjoin(Drone, Mission.drone_id == Drone.id)
            .outerjoin(Battery, Mission.battery_id == Battery.id)
            .where(
                Mission.handling_status.in_([
                    HandlingStatus.INCOMING,
                    HandlingStatus.AT_HUB,
                    HandlingStatus.READY,
                    HandlingStatus.CANNOT_CONTINUE
                ])
            )
        )
        
        if user.hub_id and str(user.hub_id).isdigit():
            query = query.where(Mission.destination_hub_id == int(user.hub_id))
            
        query = query.order_by(Mission.scheduled_time.desc().nullslast())
        
        result = await db.execute(query)
        rows = result.all()
        
        response = []
        for row in rows:
            m_id, m_status, m_handling, drone_code, model, battery_code, battery_percent = row
            
            status_val = m_status.value if hasattr(m_status, "value") else m_status
            handling_val = m_handling.value if hasattr(m_handling, "value") else m_handling
            
            response.append({
                "mission_code": str(m_id),
                "drone_code": drone_code,
                "model": model,
                "mission_state": status_val if m_status else None,
                "battery_code": battery_code,
                "battery_percent": battery_percent,
                "handling_status": handling_val if m_handling else None
            })
            
        return response
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@router.post("/missions/{mission_id}/confirm-arrival")
async def confirm_arrival(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician confirm drone đã đáp hub đích — kết thúc mission hoàn toàn.

    Actions:
    - mission.status → COMPLETED, arrival_confirmed_by/at ghi nhận.
    - Tất cả MissionLeg còn lại → COMPLETED (leg cuối chưa mark).
    - Drone: current_hub_id = destination_hub_id, status → Available (hoặc Maintenance nếu có incident mở).
    - Battery (pin trên drone): drone_id = None, current_hub_id = destination_hub_id (tháo khỏi drone, đưa về hub sạc).
    - Order: status → DELIVERED_TO_HUB, gửi email thông báo.
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus, Order as OrderModel, OrderStatus, Incident, IncidentStatus
    from app.modules.fleet.models import DroneStatus
    from app.core.email import send_hub_arrival_email
    from app.modules.system.models import Hub as HubModel

    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.arrival_confirmed_by:
        raise HTTPException(status_code=400, detail="Arrival already confirmed")

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # ── 1. Cập nhật Mission ───────────────────────────────────────────────────
    mission.arrival_confirmed_by = user.id
    mission.arrival_confirmed_at = now
    mission.arrived_at = now           # trigger pickup reminder countdown (3 ngày)
    mission.status = MissionStatus.COMPLETED
    mission.end_time = now

    # ── 2. Mark tất cả MissionLeg còn lại → COMPLETED ────────────────────────
    legs_result = await db.execute(
        select(MissionLeg).where(MissionLeg.mission_id == mission_id)
    )
    legs = legs_result.scalars().all()
    for leg in legs:
        if leg.status != MissionLegStatus.COMPLETED:
            leg.status = MissionLegStatus.COMPLETED
            leg.completed_at = now

    # ── 3. Drone: cập nhật hub, reset status ─────────────────────────────────
    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
    if drone and mission.destination_hub_id:
        drone.current_hub_id = mission.destination_hub_id

        # Kiểm tra có incident OPEN hoặc INVESTIGATING không → Maintenance, ngược lại → Available
        incident_result = await db.execute(
            select(Incident).where(
                Incident.mission_id == mission_id,
                Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.INVESTIGATING])
            ).limit(1)
        )
        has_open_incident = incident_result.scalars().first() is not None
        drone.status = DroneStatus.MAINTENANCE if has_open_incident else DroneStatus.AVAILABLE

    # ── 4. Battery: tháo khỏi drone, đưa về hub để sạc ──────────────────────
    if mission.battery_id:
        battery = await db.get(Battery, mission.battery_id)
        if battery:
            battery.drone_id = None
            battery.current_hub_id = mission.destination_hub_id  # về hub đích để sạc

    # ── 5. Order: DELIVERED_TO_HUB + Email ───────────────────────────────────
    try:
        if mission.order_id:
            order_result = await db.execute(
                select(OrderModel).where(OrderModel.id == mission.order_id)
            )
            order_obj = order_result.scalar_one_or_none()
            if order_obj:
                order_obj.status = OrderStatus.DELIVERED_TO_HUB
                order_obj.destination_received_at = now
                order_obj.destination_received_by = user.id
                order_obj.updated_at = now

                hub_name = None
                if mission.destination_hub_id:
                    hub = await db.get(HubModel, mission.destination_hub_id)
                    hub_name = hub.name if hub else None

                if order_obj.receiver_email:
                    await send_hub_arrival_email(
                        to_email=order_obj.receiver_email,
                        order_id=order_obj.id,
                        hub_name=hub_name,
                        frontend_url=settings.FRONTEND_URL,
                    )
    except Exception as e:
        print(f"[EMAIL] Lỗi DELIVERED_TO_HUB: {e}")

    await db.commit()

    return {
        "message": "Arrival confirmed. Mission hoàn tất.",
        "mission_id": mission_id,
        "confirmed_by": user.id,
        "drone_status": drone.status.value if drone else None,
        "battery_released": mission.battery_id,
        "legs_completed": len(legs),
    }


# ── Drone Profile ─────────────────────────────────────────────────────────────

@router.get("/drones", response_model=list[DroneProfileResponse])
async def list_drones(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """Chỉ trả về drone tại hub của technician đang đăng nhập."""
    query = (
        select(
            Drone,
            Hub.name.label("hub_name"),
            Battery.id.label("installed_battery_id"),
            Battery.serial_number.label("battery_code")
        )
        .outerjoin(Hub, Drone.current_hub_id == Hub.id)
        .outerjoin(Battery, Battery.drone_id == Drone.id)
        .order_by(Drone.id)
    )

    # Filter theo hub của technician
    if user.hub_id:
        query = query.where(Drone.current_hub_id == int(user.hub_id))

    result = await db.execute(query)
    rows = result.all()
    
    response = []
    for row in rows:
        drone, hub_name, installed_battery_id, battery_code = row
        drone_dict = drone.__dict__.copy()
        drone_dict["hub_name"] = hub_name
        drone_dict["installed_battery_id"] = installed_battery_id
        drone_dict["battery_code"] = battery_code
        response.append(drone_dict)
    
    return response


@router.get("/drones/{drone_id}", response_model=DroneProfileResponse)
async def get_drone_profile(
    drone_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(
            Drone,
            Hub.name.label("hub_name"),
            Battery.id.label("installed_battery_id"),
            Battery.serial_number.label("battery_code")
        )
        .outerjoin(Hub, Drone.current_hub_id == Hub.id)
        .outerjoin(Battery, Battery.drone_id == Drone.id)
        .where(Drone.id == drone_id)
    )
    # Technician chỉ xem drone tại hub của mình
    if user.hub_id:
        query = query.where(Drone.current_hub_id == int(user.hub_id))
    result = await db.execute(query)
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Drone not found or not at your hub")
        
    drone, hub_name, installed_battery_id, battery_code = row
    drone_dict = drone.__dict__.copy()
    drone_dict["hub_name"] = hub_name
    drone_dict["installed_battery_id"] = installed_battery_id
    drone_dict["battery_code"] = battery_code
    
    # last_flight_at
    mission_res = await db.execute(
        select(Mission.arrived_at)
        .where(Mission.drone_id == drone_id, Mission.status == MissionStatus.COMPLETED)
        .order_by(Mission.arrived_at.desc())
        .limit(1)
    )
    drone_dict["last_flight_at"] = mission_res.scalar_one_or_none()
    
    # next_maintenance_at
    schedule_res = await db.execute(
        select(MaintenanceSchedule.next_inspection_at)
        .where(MaintenanceSchedule.drone_id == drone_id)
        .order_by(MaintenanceSchedule.next_inspection_at.asc())
        .limit(1)
    )
    drone_dict["next_maintenance_at"] = schedule_res.scalar_one_or_none()
    
    # last_maintenance_at
    wo_res = await db.execute(
        select(WorkOrder.completed_at)
        .where(WorkOrder.drone_id == drone_id, WorkOrder.status == WorkOrderStatus.COMPLETED)
        .order_by(WorkOrder.completed_at.desc())
        .limit(1)
    )
    drone_dict["last_maintenance_at"] = wo_res.scalar_one_or_none()
    
    return drone_dict


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


def get_estimated_charging_minutes(current_charge: int, cycle_duration_min: int = 15) -> int:
    if current_charge is None or current_charge >= 100: 
        return 0
    cycles = 0
    charge = float(current_charge)
    while charge < 99.0:
        if charge < 80:
            charge += 40
        else:
            charge += 0.5 * (100 - charge)
        cycles += 1
    return cycles * cycle_duration_min


@router.get("/batteries", response_model=list[BatteryResponse])
async def get_hub_batteries(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    if not user.hub_id:
        return []
    
    hub_id = int(user.hub_id) if str(user.hub_id).isdigit() else user.hub_id
    
    result = await db.execute(
        select(Battery).where(Battery.current_hub_id == hub_id).order_by(Battery.id)
    )
    batteries = result.scalars().all()
    
    response = []
    for b in batteries:
        mins = get_estimated_charging_minutes(b.charge_level_pct) if b.status == "Charging" or (b.charge_level_pct and b.charge_level_pct < 100 and b.drone_id is None) else 0
        response.append({
            "id": b.id,
            "serial_number": b.serial_number,
            "battery_model": b.battery_model,
            "capacity_wh": b.capacity_wh,
            "status": b.status,
            "drone_id": b.drone_id,
            "current_hub_id": b.current_hub_id,
            "charge_level_pct": b.charge_level_pct,
            "estimated_minutes_remaining": mins
        })
    return response


@router.post("/batteries/{battery_id}/use")
async def use_battery_for_mission(
    battery_id: str,
    body: BatteryUseRequest,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, str(battery_id))
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
        
    mission = await db.get(Mission, body.mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
        
    mission.battery_id = battery.id
    battery.status = "In Use"
    await db.commit()
    
    return {"message": "Battery assigned successfully"}


@router.post("/batteries/{battery_id}/mark-charged")
async def mark_battery_charged(
    battery_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Đánh dấu một viên pin đã sạc đầy (100%).
    GHI CHÚ: Mặc dù hệ thống đã có Worker tự động sạc pin theo thời gian (background job), 
    API này vẫn được giữ lại để đóng vai trò "Manual Override" (Ghi đè thủ công) phục vụ:
    1. Xử lý ngoại lệ ngoài đời thực (cảm biến IoT hỏng, Technician nhập pin mới kho vào).
    2. Sạc nhanh (Fast Charge) khẩn cấp.
    3. Phục vụ việc Demo đồ án nhánh chóng (không cần chờ chu kỳ Cronjob tự sạc).
    """
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
        
    # Tùy chọn: có thể kiểm tra xem pin có đúng ở hub của technician không
    # if user.hub_id and str(battery.current_hub_id) != str(user.hub_id):
    #     raise HTTPException(status_code=403, detail="Battery is not at your hub")
        
    battery.charge_level_pct = 100
    await db.commit()
    await db.refresh(battery)
    
    return {
        "message": "Đã cập nhật pin sạc đầy 100%",
        "battery": {
            "id": battery.id,
            "serial_number": battery.serial_number,
            "charge_level_pct": battery.charge_level_pct,
            "current_hub_id": battery.current_hub_id
        }
    }


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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    next_inspection = now + timedelta(days=body.interval_days) if body.interval_days else None
    schedule = MaintenanceSchedule(
        drone_id=body.drone_id,
        maintenance_type=body.maintenance_type,
        interval_days=body.interval_days,
        interval_flight_hours=body.interval_flight_hours,
        next_inspection_at=next_inspection,
        status="active",
        created_at=now,
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
    query = (
        select(
            WorkOrder,
            Drone.name.label("drone_code"),
            Drone.model.label("model"),
            User.full_name.label("assigned_technician_name")
        )
        .outerjoin(Drone, WorkOrder.drone_id == Drone.id)
        .outerjoin(User, cast(WorkOrder.technician_id, String) == User.id)
        .order_by(WorkOrder.id.desc())
    )
    result = await db.execute(query)
    rows = result.all()
    
    response = []
    for row in rows:
        wo, drone_code, model, assigned_technician_name = row
        wo_dict = wo.__dict__.copy()
        wo_dict["drone_code"] = drone_code
        wo_dict["model"] = model
        wo_dict["assigned_technician_name"] = assigned_technician_name
        response.append(wo_dict)
    
    return response


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderResponse)
async def get_work_order(
    work_order_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(
            WorkOrder,
            Drone.name.label("drone_code"),
            Drone.model.label("model"),
            User.full_name.label("assigned_technician_name")
        )
        .outerjoin(Drone, WorkOrder.drone_id == Drone.id)
        .outerjoin(User, cast(WorkOrder.technician_id, String) == User.id)
        .where(WorkOrder.id == work_order_id)
    )
    result = await db.execute(query)
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Work order not found")
        
    wo, drone_code, model, assigned_technician_name = row
    wo_dict = wo.__dict__.copy()
    wo_dict["drone_code"] = drone_code
    wo_dict["model"] = model
    wo_dict["assigned_technician_name"] = assigned_technician_name
    
    return wo_dict


@router.post("/work-orders", response_model=WorkOrderResponse, status_code=201)
async def create_work_order(
    body: WorkOrderCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = WorkOrder(
        drone_id=body.drone_id,
        battery_id=body.battery_id,
        technician_id=user.id,
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

# ── Relay Hub Flow (Transit Missions) ────────────────────────────────────────

@router.post("/missions/{mission_id}/confirm-relay-arrival", summary="Confirm drone đáp relay hub trung gian")
async def confirm_relay_arrival(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician confirm drone đã đáp xuống relay hub trung gian trong chuyến bay multi-leg.

    Actions:
    - Validate mission đang IN_PROGRESS và có relay hub (nhiều legs).
    - Tìm MissionLeg đang IN_PROGRESS hoặc PENDING đầu tiên chưa completed.
    - Mark leg hiện tại → COMPLETED.
    - Cập nhật mission.handling_status → AT_HUB (chờ swap pin).
    - Cập nhật drone.current_hub_id → hub trung gian vừa đáp.
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus
    from app.modules.fleet.models import DroneStatus

    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status not in [MissionStatus.IN_PROGRESS, MissionStatus.FLYING, MissionStatus.ACTIVE_MISSION]:
        raise HTTPException(
            status_code=400,
            detail=f"Mission đang ở trạng thái '{mission.status.value}', không thể confirm relay arrival. Mission phải đang IN_PROGRESS/FLYING."
        )

    # Lấy tất cả legs của mission, theo thứ tự
    legs_result = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id == mission_id)
        .order_by(MissionLeg.sequence_no)
    )
    legs = legs_result.scalars().all()

    if len(legs) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Đây là tuyến bay thẳng (Direct Route), không có relay hub. Dùng confirm-arrival thay thế."
        )

    # Tìm leg đang active (IN_PROGRESS) hoặc PENDING đầu tiên chưa completed
    current_leg = None
    next_leg = None
    for i, leg in enumerate(legs):
        if leg.status in [MissionLegStatus.IN_PROGRESS, MissionLegStatus.PENDING]:
            current_leg = leg
            if i + 1 < len(legs):
                next_leg = legs[i + 1]
            break

    if not current_leg:
        raise HTTPException(
            status_code=400,
            detail="Không tìm thấy chặng bay đang hoạt động. Tất cả legs đã hoàn thành?"
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Không phải leg cuối cùng → đây là relay hub, không phải hub đích
    if next_leg is None:
        raise HTTPException(
            status_code=400,
            detail="Đây là chặng cuối cùng. Dùng confirm-arrival để kết thúc mission thay thế."
        )

    # Cập nhật leg hiện tại → COMPLETED
    current_leg.status = MissionLegStatus.COMPLETED
    current_leg.completed_at = now

    # Cập nhật drone về relay hub (to_hub_id của leg vừa completed)
    relay_hub_id = current_leg.to_hub_id
    if mission.drone_id and relay_hub_id:
        drone = await db.get(Drone, mission.drone_id)
        if drone:
            drone.current_hub_id = relay_hub_id

    # Cập nhật mission handling_status → AT_HUB (chờ Technician swap pin)
    mission.handling_status = HandlingStatus.AT_HUB

    await db.commit()

    relay_hub = await db.get(Hub, relay_hub_id) if relay_hub_id else None
    return {
        "message": "Relay arrival confirmed. Drone đang chờ swap pin tại relay hub.",
        "mission_id": mission_id,
        "completed_leg": {
            "id": current_leg.id,
            "sequence_no": current_leg.sequence_no,
            "from_hub_id": current_leg.from_hub_id,
            "to_hub_id": current_leg.to_hub_id,
            "status": current_leg.status.value,
        },
        "next_leg": {
            "id": next_leg.id,
            "sequence_no": next_leg.sequence_no,
            "from_hub_id": next_leg.from_hub_id,
            "to_hub_id": next_leg.to_hub_id,
        },
        "relay_hub": relay_hub.name if relay_hub else str(relay_hub_id),
        "handling_status": mission.handling_status.value,
    }


from pydantic import BaseModel

class SwapBatteryRequest(BaseModel):
    new_battery_id: str


@router.post("/missions/{mission_id}/swap-battery", summary="Thay pin tại relay hub trung gian")
async def swap_battery_at_relay(
    mission_id: str,
    body: SwapBatteryRequest,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician thay pin cho drone tại relay hub trung gian.

    Yêu cầu:
    - Mission phải đang IN_PROGRESS, handling_status = AT_HUB (đã confirm relay arrival).
    - Pin mới phải: tại đúng relay hub, sạc 100%, status ACTIVE, chưa gắn drone khác, model tương thích.

    Actions:
    - Tháo pin cũ: battery.drone_id = None, battery.current_hub_id = relay_hub_id.
    - Gán pin mới: battery.drone_id = mission.drone_id, mission.battery_id = new_battery_id.
    - Cập nhật leg tiếp theo: battery_id = new_battery_id.
    - Ghi MissionBatterySwapHistory.
    - mission.handling_status → READY (sẵn sàng dispatch).
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus, MissionBatterySwapHistory
    from app.modules.fleet.models import DroneStatus, batteries_status
    from app.modules.missions.technician.router import get_compatible_battery_models

    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.handling_status != HandlingStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail=f"Mission phải ở trạng thái AT_HUB (đã confirm relay arrival) trước khi swap pin. Hiện tại: {mission.handling_status}."
        )

    # Lấy relay hub hiện tại từ drone
    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
    if not drone:
        raise HTTPException(status_code=400, detail="Mission chưa được gán drone.")
    relay_hub_id = drone.current_hub_id
    if not relay_hub_id:
        raise HTTPException(status_code=400, detail="Không xác định được relay hub hiện tại của drone.")

    # Validate pin mới
    new_battery = await db.get(Battery, body.new_battery_id)
    if not new_battery:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy pin '{body.new_battery_id}'.")

    if new_battery.current_hub_id != relay_hub_id:
        raise HTTPException(
            status_code=400,
            detail=f"Pin {new_battery.id} không thuộc relay hub hiện tại (Hub {relay_hub_id})."
        )
    if new_battery.status != batteries_status.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Pin {new_battery.id} không ở trạng thái Active.")
    if (new_battery.charge_level_pct or 0) < 100:
        raise HTTPException(
            status_code=400,
            detail=f"Pin {new_battery.id} chưa sạc đủ 100% (hiện {new_battery.charge_level_pct}%)."
        )
    if new_battery.drone_id is not None and new_battery.drone_id != mission.drone_id:
        raise HTTPException(status_code=400, detail=f"Pin {new_battery.id} đang gắn trên drone khác.")

    # Validate model tương thích
    compatible_models = await get_compatible_battery_models(db, drone.model)
    if compatible_models and new_battery.battery_model and new_battery.battery_model not in compatible_models:
        raise HTTPException(
            status_code=400,
            detail=f"Pin {new_battery.id} (model: {new_battery.battery_model}) không tương thích với {drone.model}. Cần: {', '.join(compatible_models)}."
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_battery_id = mission.battery_id

    # Tháo pin cũ
    if old_battery_id and old_battery_id != body.new_battery_id:
        old_battery = await db.get(Battery, old_battery_id)
        if old_battery:
            old_battery.drone_id = None
            old_battery.current_hub_id = relay_hub_id  # pin cũ ở lại relay hub để sạc

    # Gán pin mới
    new_battery.drone_id = mission.drone_id
    new_battery.current_hub_id = relay_hub_id
    mission.battery_id = new_battery.id

    # Cập nhật battery_id cho leg tiếp theo (PENDING)
    next_leg_result = await db.execute(
        select(MissionLeg)
        .where(
            MissionLeg.mission_id == mission_id,
            MissionLeg.status == MissionLegStatus.PENDING,
        )
        .order_by(MissionLeg.sequence_no)
        .limit(1)
    )
    next_leg = next_leg_result.scalars().first()
    if next_leg:
        next_leg.battery_id = new_battery.id

    # Ghi lịch sử swap
    swap_record = MissionBatterySwapHistory(
        mission_id=mission_id,
        hub_id=relay_hub_id,
        old_battery_id=old_battery_id,
        new_battery_id=new_battery.id,
        swapped_at=now,
    )
    db.add(swap_record)

    # Cập nhật handling_status → READY
    mission.handling_status = HandlingStatus.READY

    await db.commit()

    return {
        "message": "Swap pin thành công. Drone sẵn sàng bay tiếp.",
        "mission_id": mission_id,
        "relay_hub_id": relay_hub_id,
        "old_battery_id": old_battery_id,
        "new_battery_id": new_battery.id,
        "new_battery_serial": new_battery.serial_number,
        "handling_status": mission.handling_status.value,
    }


@router.post("/missions/{mission_id}/dispatch", summary="Dispatch drone bay tiếp chặng kế tiếp")
async def dispatch_drone(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician dispatch drone bay tiếp chặng kế tiếp sau khi swap pin tại relay hub.
    Technician không cần chờ Operator phê duyệt lại.

    Yêu cầu:
    - Mission đang IN_PROGRESS, handling_status = READY (đã swap pin xong).

    Actions:
    - Leg tiếp theo (PENDING) → IN_PROGRESS, started_at = now.
    - mission.handling_status → INCOMING (drone đang bay, không còn ở hub).
    - Drone.status → In Flight, drone.current_hub_id = None.
    - Notify Operator qua log (ghi AIRecommendation hoặc tương tự).
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus
    from app.modules.fleet.models import DroneStatus

    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.handling_status != HandlingStatus.READY:
        raise HTTPException(
            status_code=400,
            detail=f"Mission phải ở trạng thái READY (đã swap pin) trước khi dispatch. Hiện tại: {mission.handling_status}."
        )

    # Tìm leg tiếp theo cần bay (PENDING)
    next_leg_result = await db.execute(
        select(MissionLeg)
        .where(
            MissionLeg.mission_id == mission_id,
            MissionLeg.status == MissionLegStatus.PENDING,
        )
        .order_by(MissionLeg.sequence_no)
        .limit(1)
    )
    next_leg = next_leg_result.scalars().first()
    if not next_leg:
        raise HTTPException(
            status_code=400,
            detail="Không còn chặng bay nào ở trạng thái PENDING. Tất cả legs đã hoàn thành?"
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Chuyển leg sang IN_PROGRESS
    next_leg.status = MissionLegStatus.IN_PROGRESS
    next_leg.started_at = now

    # Drone cất cánh → In Flight, rời hub
    if mission.drone_id:
        drone = await db.get(Drone, mission.drone_id)
        if drone:
            drone.status = DroneStatus.IN_FLIGHT
            drone.current_hub_id = None

    # Mission handling → INCOMING (drone đang bay, không ở hub)
    mission.handling_status = HandlingStatus.INCOMING

    await db.commit()

    # Thông tin leg đang bay và leg tiếp theo (nếu có)
    remaining_legs_result = await db.execute(
        select(MissionLeg)
        .where(
            MissionLeg.mission_id == mission_id,
            MissionLeg.status == MissionLegStatus.PENDING,
        )
        .order_by(MissionLeg.sequence_no)
    )
    remaining = remaining_legs_result.scalars().all()

    from app.modules.system.models import Hub as HubModel
    to_hub = await db.get(HubModel, next_leg.to_hub_id) if next_leg.to_hub_id else None

    return {
        "message": f"Drone đã được dispatch. Bay tiếp chặng {next_leg.sequence_no} → {to_hub.name if to_hub else next_leg.to_hub_id}.",
        "mission_id": mission_id,
        "dispatched_leg": {
            "id": next_leg.id,
            "sequence_no": next_leg.sequence_no,
            "from_hub_id": next_leg.from_hub_id,
            "to_hub_id": next_leg.to_hub_id,
            "status": next_leg.status.value,
            "started_at": now.isoformat(),
        },
        "remaining_legs": len(remaining),
        "handling_status": mission.handling_status.value,
        "notify_operator": f"Drone #{mission.drone_id} đã cất cánh từ relay hub, đang bay chặng {next_leg.sequence_no}/{next_leg.sequence_no + len(remaining)}.",
    }

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
