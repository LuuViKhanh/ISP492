import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, cast, String

from app.core.timezone import get_vn_naive_datetime, get_vn_datetime

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


from typing import Optional
from pydantic import BaseModel


class ConfirmArrivalRequest(BaseModel):
    has_issue: Optional[bool] = False
    has_incident: Optional[bool] = False
    issue_description: Optional[str] = None
    drone_status: Optional[str] = None
    battery_faulty: Optional[bool] = False


async def _resolve_mission(db: AsyncSession, mission_or_order_id: str) -> Mission:
    from sqlalchemy import func
    clean_id = (mission_or_order_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="mission_id is required")

    mission = await db.get(Mission, clean_id)
    if not mission:
        res = await db.execute(
            select(Mission)
            .where(
                (Mission.id == clean_id)
                | (Mission.order_id == clean_id)
                | (func.upper(func.trim(Mission.id)) == clean_id.upper())
                | (func.upper(func.trim(Mission.order_id)) == clean_id.upper())
            )
            .order_by(Mission.id.desc())
        )
        mission = res.scalars().first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


# ── Incoming & Confirm Arrival ───────────────────────────────────────────────

@router.get("/incoming")
async def get_incoming_drones(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy import cast, String, or_
    from app.modules.missions.models import MissionLeg
    import traceback
    try:
        query = (
            select(
                Mission.id,
                Mission.order_id,
                Mission.origin_hub_id,
                Mission.destination_hub_id,
                cast(Mission.status, String).label("status"),
                cast(Mission.handling_status, String).label("handling_status"),
                Drone.id.label("drone_id"),
                Drone.name.label("drone_code"),
                Drone.model.label("model"),
                Drone.current_hub_id.label("drone_current_hub_id"),
                Battery.id.label("battery_id"),
                Battery.serial_number.label("battery_code"),
                Battery.charge_level_pct.label("battery_percent"),
                Mission.arrival_confirmed_at,
            )
            .outerjoin(Drone, Mission.drone_id == Drone.id)
            .outerjoin(Battery, Mission.battery_id == Battery.id)
            .where(
                Mission.status.in_([
                    MissionStatus.FLYING,
                    MissionStatus.INCIDENT_RETURN,
                    MissionStatus.COMPLETED,
                    MissionStatus.MISSION_COMPLETED,
                ]),
                Mission.handling_status.in_([
                    HandlingStatus.INCOMING,
                    HandlingStatus.AT_HUB,
                    HandlingStatus.CANNOT_CONTINUE,
                ])
            )
        )
        
        if user.hub_id and str(user.hub_id).isdigit():
            tech_hub_id = int(user.hub_id)
            leg_mission_ids_subq = (
                select(MissionLeg.mission_id)
                .where(
                    or_(
                        MissionLeg.to_hub_id == tech_hub_id,
                        MissionLeg.from_hub_id == tech_hub_id,
                    )
                )
            )
            query = query.where(
                or_(
                    Mission.destination_hub_id == tech_hub_id,
                    Drone.current_hub_id == tech_hub_id,
                    Mission.id.in_(leg_mission_ids_subq),
                )
            )
            
        query = query.order_by(Mission.scheduled_time.desc().nullslast(), Mission.id.desc())
        
        result = await db.execute(query)
        rows = result.all()
        
        response = []
        seen_ids = set()
        for row in rows:
            (
                m_id,
                order_id,
                origin_hub_id,
                dest_hub_id,
                m_status,
                m_handling,
                drone_id,
                drone_code,
                model,
                drone_curr_hub_id,
                battery_id,
                battery_code,
                battery_percent,
                arrival_confirmed_at,
            ) = row
            if m_id in seen_ids:
                continue
            seen_ids.add(m_id)
            
            handling_val = m_handling.value if hasattr(m_handling, "value") else m_handling

            # Map về 3 trạng thái cố định cho FE
            if m_status == MissionStatus.INCIDENT_RETURN or m_handling == HandlingStatus.CANNOT_CONTINUE:
                mission_state = "Incident"
            elif m_status in (MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED):
                mission_state = "Completed"
            elif m_status == MissionStatus.FLYING:
                mission_state = "Flying"
            else:
                mission_state = None

            response.append({
                "id": str(m_id),
                "mission_id": str(m_id),
                "mission_code": str(m_id),
                "order_id": order_id,
                "origin_hub_id": origin_hub_id,
                "destination_hub_id": dest_hub_id,
                "drone_id": drone_id,
                "drone_code": drone_code,
                "drone_current_hub_id": drone_curr_hub_id,
                "model": model,
                "mission_state": mission_state,
                "battery_id": battery_id,
                "battery_code": battery_code,
                "battery_percent": battery_percent,
                "handling_status": handling_val if m_handling else None,
                "arrival_confirmed_at": arrival_confirmed_at.isoformat() if arrival_confirmed_at else None,
            })
            
        return response
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@router.get("/history", summary="Lịch sử các chuyến bay đã hoàn thành tiếp nhận tại Hub")
async def get_transit_history(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy import cast, String, or_
    from app.modules.missions.models import MissionLeg
    import traceback
    try:
        query = (
            select(
                Mission.id,
                Mission.order_id,
                Mission.origin_hub_id,
                Mission.destination_hub_id,
                Mission.arrived_at,
                Mission.end_time,
                cast(Mission.status, String).label("status"),
                cast(Mission.handling_status, String).label("handling_status"),
                Drone.id.label("drone_id"),
                Drone.name.label("drone_code"),
                Drone.model.label("model"),
                Battery.id.label("battery_id"),
                Battery.serial_number.label("battery_code"),
                Battery.charge_level_pct.label("battery_percent"),
                Mission.arrival_confirmed_at,
            )
            .outerjoin(Drone, Mission.drone_id == Drone.id)
            .outerjoin(Battery, Mission.battery_id == Battery.id)
            .where(
                Mission.status.in_([
                    MissionStatus.COMPLETED,
                    MissionStatus.MISSION_COMPLETED,
                ])
            )
        )

        if user.hub_id and str(user.hub_id).isdigit():
            tech_hub_id = int(user.hub_id)
            leg_mission_ids_subq = (
                select(MissionLeg.mission_id)
                .where(
                    or_(
                        MissionLeg.to_hub_id == tech_hub_id,
                        MissionLeg.from_hub_id == tech_hub_id,
                    )
                )
            )
            query = query.where(
                or_(
                    Mission.destination_hub_id == tech_hub_id,
                    Mission.origin_hub_id == tech_hub_id,
                    Mission.id.in_(leg_mission_ids_subq),
                )
            )

        query = query.order_by(Mission.arrived_at.desc().nullslast(), Mission.id.desc())
        result = await db.execute(query)
        rows = result.all()

        response = []
        for row in rows:
            (
                m_id,
                order_id,
                origin_hub_id,
                dest_hub_id,
                arrived_at,
                end_time,
                m_status,
                m_handling,
                drone_id,
                drone_code,
                model,
                battery_id,
                battery_code,
                battery_percent,
                arrival_confirmed_at,
            ) = row
            status_val = m_status.value if hasattr(m_status, "value") else m_status
            handling_val = m_handling.value if hasattr(m_handling, "value") else m_handling
            response.append({
                "id": str(m_id),
                "mission_id": str(m_id),
                "mission_code": str(m_id),
                "order_id": order_id,
                "origin_hub_id": origin_hub_id,
                "destination_hub_id": dest_hub_id,
                "arrived_at": arrived_at.isoformat() if arrived_at else None,
                "end_time": end_time.isoformat() if end_time else None,
                "drone_id": drone_id,
                "drone_code": drone_code,
                "model": model,
                "mission_state": status_val if m_status else None,
                "status": status_val if m_status else None,
                "battery_id": battery_id,
                "battery_code": battery_code,
                "battery_percent": battery_percent,
                "handling_status": handling_val if m_handling else None,
                "arrival_confirmed_at": arrival_confirmed_at.isoformat() if arrival_confirmed_at else None,
            })
        return response
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@router.post("/missions/{mission_id}/confirm-arrival")
async def confirm_arrival(
    mission_id: str,
    body: Optional[ConfirmArrivalRequest] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician confirm drone đã đáp hub đích — kết thúc mission hoàn toàn (Issue #4).

    Actions:
    - mission.status → COMPLETED, arrived_at/end_time ghi nhận, handling_status → None (chuyển khỏi danh sách Transit sang History).
    - Tất cả MissionLeg còn lại → COMPLETED (leg cuối chưa mark).
    - Drone: được tiếp nhận và kiểm tra sơ bộ; current_hub_id = destination_hub_id;
      không có lỗi → lưu vào Hub Fleet với status Available;
      có incident hoặc phát hiện lỗi (body.has_issue / has_incident) → lưu vào Hub Fleet với status Maintenance.
    - Battery (pin trên drone): tháo khỏi drone (drone_id = None), current_hub_id = destination_hub_id
      (chuyển sang Battery Management để sạc hoặc kiểm tra nếu có vấn đề).
    - Order: status → DELIVERED_TO_HUB, gửi email thông báo.
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus, Order as OrderModel, OrderStatus, Incident, IncidentStatus
    from app.modules.fleet.models import DroneStatus, batteries_status
    from app.core.email import send_hub_arrival_email
    from app.modules.system.models import Hub as HubModel

    mission = await _resolve_mission(db, mission_id)

    # Nếu mission đã xử lý hoàn tất trước đó (handling_status đã được xóa và drone đã được tiếp nhận)
    if (
        mission.status in [MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED]
        and mission.handling_status is None
        and mission.arrived_at is not None
    ):
        raise HTTPException(status_code=400, detail="Arrival already confirmed")

    now = get_vn_naive_datetime()
    dest_hub_id = mission.destination_hub_id or (int(user.hub_id) if (user.hub_id and str(user.hub_id).isdigit()) else None)

    # ── 1. Cập nhật Mission (rời danh sách Transit đang hoạt động sang History) ──
    if not mission.arrived_at:
        mission.arrived_at = now       # trigger pickup reminder countdown (3 ngày chờ KH lấy hàng)
    if not mission.arrival_confirmed_at:
        mission.arrival_confirmed_at = now   # ghi nhận thời điểm Technician confirm drone đáp hub
    mission.arrival_confirmed_by = str(user.id)
    if mission.status != MissionStatus.MISSION_COMPLETED:
        mission.status = MissionStatus.COMPLETED
    mission.end_time = mission.end_time or now
    mission.handling_status = None     # xóa handling_status để không còn nằm trong /incoming (Transit)

    # ── 2. Mark tất cả MissionLeg còn lại → COMPLETED ────────────────────────
    legs_result = await db.execute(
        select(MissionLeg).where(MissionLeg.mission_id == mission.id)
    )
    legs = legs_result.scalars().all()
    for leg in legs:
        if leg.status != MissionLegStatus.COMPLETED:
            leg.status = MissionLegStatus.COMPLETED
            leg.completed_at = now

    # ── 3. Drone: tiếp nhận & kiểm tra sơ bộ → Available hoặc Maintenance ────
    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
    has_issue_flag = False
    if body:
        if body.has_issue or body.has_incident:
            has_issue_flag = True
        if body.drone_status and str(body.drone_status).strip().lower() == "maintenance":
            has_issue_flag = True

    if drone:
        if dest_hub_id:
            drone.current_hub_id = dest_hub_id

        # Kiểm tra có incident OPEN hoặc INVESTIGATING không, hoặc Technician báo lỗi
        incident_result = await db.execute(
            select(Incident).where(
                Incident.mission_id == mission.id,
                Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.INVESTIGATING])
            ).limit(1)
        )
        has_open_incident = incident_result.scalars().first() is not None
        drone.status = DroneStatus.MAINTENANCE if (has_open_incident or has_issue_flag) else DroneStatus.AVAILABLE

    # ── 4. Battery: tháo khỏi drone, chuyển sang Battery Management tại Hub đích ──
    released_battery_id = mission.battery_id
    if mission.battery_id:
        battery = await db.get(Battery, mission.battery_id)
        if battery:
            battery.drone_id = None
            if dest_hub_id:
                battery.current_hub_id = dest_hub_id
            if body and body.battery_faulty:
                battery.status = batteries_status.MAINTENANCE

    # Đảm bảo bất kỳ viên pin nào khác đang gắn trên drone này cũng được tháo ra đưa về Battery Management
    if mission.drone_id:
        attached_bats_res = await db.execute(
            select(Battery).where(Battery.drone_id == mission.drone_id)
        )
        for b in attached_bats_res.scalars().all():
            b.drone_id = None
            if dest_hub_id:
                b.current_hub_id = dest_hub_id
            if body and body.battery_faulty:
                b.status = batteries_status.MAINTENANCE
            if not released_battery_id:
                released_battery_id = b.id

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
                order_obj.destination_received_by = str(user.id)
                order_obj.updated_at = now

                hub_name = None
                if dest_hub_id:
                    hub = await db.get(HubModel, dest_hub_id)
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
        "message": "Arrival confirmed. Mission hoàn tất, Drone đã đưa vào Hub Fleet và Pin đã chuyển sang Battery Management.",
        "mission_id": mission.id,
        "order_id": mission.order_id,
        "mission_status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        "handling_status": None,
        "confirmed_by": user.id,
        "arrival_confirmed_at": mission.arrival_confirmed_at.isoformat() if mission.arrival_confirmed_at else None,
        "drone_id": drone.id if drone else None,
        "drone_status": drone.status.value if drone else None,
        "battery_released": released_battery_id,
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
    drone_id: str,
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
    drone_id: str,
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
    drone_id: str,
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
    drone_id: str,
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
        created_at=get_vn_naive_datetime(),
    )
    db.add(record)
    await db.flush()

    items = []
    for item in (body.inspection_items or []):
        inspection_item = MaintenanceInspectionItem(
            maintenance_record_id=record.id,
            item_name=item.item_name,
            is_completed=item.is_completed,
            checked_at=get_vn_naive_datetime() if item.is_completed else None,
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
    drone_id: str,
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
    now = get_vn_naive_datetime()
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
    """Chỉ trả alerts chưa được xử lý (chưa có work order)."""
    result = await db.execute(
        select(MaintenanceAlert)
        .where(MaintenanceAlert.work_order_id == None)
        .order_by(MaintenanceAlert.id.desc())
    )
    return result.scalars().all()


@router.get("/maintenance-alerts/history", response_model=list[MaintenanceAlertResponse])
async def list_maintenance_alerts_history(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """Trả alerts đã được xử lý (đã có work order)."""
    result = await db.execute(
        select(MaintenanceAlert)
        .where(MaintenanceAlert.work_order_id != None)
        .order_by(MaintenanceAlert.id.desc())
    )
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


@router.patch("/maintenance-alerts/{alert_id}/acknowledge", response_model=MaintenanceAlertResponse)
async def acknowledge_alert(
    alert_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **Acknowledge một MaintenanceAlert.**

    Technician xác nhận đã nhận và đang xử lý alert này.
    - Chuyển status từ `Pending` → `Acknowledged`.
    - Lưu `acknowledged_by` (user.id) và `acknowledged_at` (now).
    - Không thể acknowledge alert đã `Handled` hoặc đã được người khác acknowledge.
    - Idempotent: nếu cùng user acknowledge lại thì trả về alert hiện tại (không lỗi).
    """
    alert = await db.get(MaintenanceAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.status == "Handled":
        raise HTTPException(status_code=400, detail="Alert đã được xử lý (Handled), không thể acknowledge.")

    # Idempotent: nếu cùng user acknowledge lại thì trả về luôn
    if alert.status == "Acknowledged" and alert.acknowledged_by == user.id:
        return alert

    # Người khác đã acknowledge rồi
    if alert.status == "Acknowledged" and alert.acknowledged_by and alert.acknowledged_by != user.id:
        raise HTTPException(
            status_code=409,
            detail=f"Alert này đã được acknowledge bởi technician khác (ID: {alert.acknowledged_by})."
        )

    now = get_vn_naive_datetime()
    alert.status = "Acknowledged"
    alert.acknowledged_by = user.id
    alert.acknowledged_at = now

    await db.commit()
    await db.refresh(alert)
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
    await db.flush()  # lấy wo.id trước khi commit

    # Nếu tạo từ một alert → gắn work_order_id vào alert, chuyển alert sang history
    if body.alert_id:
        alert = await db.get(MaintenanceAlert, body.alert_id)
        if alert:
            alert.work_order_id = wo.id

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

    # Idempotent: nếu đã COMPLETED rồi thì không làm gì thêm
    if wo.status == WorkOrderStatus.COMPLETED and body.status == WorkOrderStatus.COMPLETED:
        return wo

    # Khi Complete: bắt buộc phải có Maintenance Record trước
    if body.status == WorkOrderStatus.COMPLETED:
        record_result = await db.execute(
            select(MaintenanceRecord).where(MaintenanceRecord.work_order_id == work_order_id).limit(1)
        )
        if not record_result.scalars().first():
            raise HTTPException(
                status_code=400,
                detail="Chưa có Maintenance Record cho Work Order này. Vui lòng tạo Maintenance Record trước khi Complete."
            )

    old_status = wo.status.value
    wo.action_taken = body.action_taken
    wo.status = body.status
    if body.status == WorkOrderStatus.COMPLETED:
        wo.resolved_at = get_vn_naive_datetime()
    log = WorkOrderLog(
        work_order_id=wo.id,
        action="inspection_update",
        from_status=old_status,
        to_status=body.status.value,
        changed_by=user.id,
        created_at=get_vn_naive_datetime(),
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
    - Validate mission đang IN_PROGRESS (hoặc SCHEDULED/FLYING/ACTIVE_MISSION) và có relay hub (nhiều legs).
    - Tìm MissionLeg đang IN_PROGRESS hoặc PENDING đầu tiên chưa completed.
    - Mark leg hiện tại → COMPLETED.
    - Cập nhật mission.handling_status → AT_HUB (chờ swap pin).
    - Cập nhật drone.current_hub_id → hub trung gian vừa đáp.
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus
    from app.modules.fleet.models import DroneStatus, batteries_status

    mission = await _resolve_mission(db, mission_id)
    if mission.status not in [
        MissionStatus.IN_PROGRESS,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
        MissionStatus.SCHEDULED,
        MissionStatus.APPROVED,
    ]:
        raise HTTPException(
            status_code=400,
            detail=f"Mission đang ở trạng thái '{mission.status.value}', không thể confirm relay arrival. Mission phải đang IN_PROGRESS/FLYING."
        )

    # Lấy tất cả legs của mission, theo thứ tự
    legs_result = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id == mission.id)
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

    now = get_vn_naive_datetime()

    # Không phải leg cuối cùng → đây là relay hub, không phải hub đích
    if next_leg is None:
        raise HTTPException(
            status_code=400,
            detail="Đây là chặng cuối cùng. Dùng confirm-arrival để kết thúc mission thay thế."
        )

    # Đảm bảo mission đang ở FLYING (khớp với bảng flow FE và live-tracking/telemetry)
    if mission.status in [MissionStatus.SCHEDULED, MissionStatus.APPROVED, MissionStatus.IN_PROGRESS]:
        mission.status = MissionStatus.FLYING
        mission.start_time = mission.start_time or now
        mission.departed_at = mission.departed_at or now

    # Cập nhật leg hiện tại → COMPLETED
    current_leg.status = MissionLegStatus.COMPLETED
    current_leg.completed_at = now

    # Cập nhật drone về relay hub (to_hub_id của leg vừa completed)
    relay_hub_id = current_leg.to_hub_id
    if mission.drone_id and relay_hub_id:
        drone = await db.get(Drone, mission.drone_id)
        if drone:
            drone.current_hub_id = relay_hub_id
            drone.status = DroneStatus.AVAILABLE

    # Tháo pin cũ khỏi drone → về Battery Management tại relay hub để sạc
    # (swap-battery sẽ lắp pin mới sau)
    if mission.battery_id and relay_hub_id:
        old_battery = await db.get(Battery, mission.battery_id)
        if old_battery:
            old_battery.drone_id = None
            old_battery.current_hub_id = relay_hub_id
            old_battery.status = batteries_status.ACTIVE

    # Cập nhật mission handling_status → AT_HUB (chờ Technician swap pin)
    mission.handling_status = HandlingStatus.AT_HUB

    await db.commit()

    relay_hub = await db.get(Hub, relay_hub_id) if relay_hub_id else None
    designated_bat = await db.get(Battery, str(next_leg.battery_id)) if (next_leg and next_leg.battery_id) else None
    return {
        "message": "Relay arrival confirmed. Drone đang chờ swap pin tại relay hub.",
        "mission_id": mission.id,
        "order_id": mission.order_id,
        "completed_leg": {
            "id": current_leg.id,
            "sequence_no": current_leg.sequence_no,
            "from_hub_id": current_leg.from_hub_id,
            "to_hub_id": current_leg.to_hub_id,
            "battery_id": current_leg.battery_id,
            "status": current_leg.status.value,
        },
        "next_leg": {
            "id": next_leg.id,
            "sequence_no": next_leg.sequence_no,
            "from_hub_id": next_leg.from_hub_id,
            "to_hub_id": next_leg.to_hub_id,
            "battery_id": next_leg.battery_id,
            "designated_battery_id": designated_bat.id if designated_bat else next_leg.battery_id,
            "designated_battery_serial": designated_bat.serial_number if designated_bat else None,
            "designated_battery_model": designated_bat.battery_model if designated_bat else None,
            "designated_battery_charge_pct": designated_bat.charge_level_pct if designated_bat else None,
        },
        "designated_battery_id": designated_bat.id if designated_bat else next_leg.battery_id,
        "designated_battery_serial": designated_bat.serial_number if designated_bat else None,
        "designated_battery_model": designated_bat.battery_model if designated_bat else None,
        "relay_hub": relay_hub.name if relay_hub else str(relay_hub_id),
        "relay_hub_id": relay_hub_id,
        "handling_status": mission.handling_status.value,
        "status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
    }


class SwapBatteryRequest(BaseModel):
    new_battery_id: Optional[str] = None
    battery_id: Optional[str] = None


@router.post("/missions/{mission_id}/swap-battery", summary="Thay pin tại relay hub trung gian")
async def swap_battery_at_relay(
    mission_id: str,
    body: Optional[SwapBatteryRequest] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician thay pin cho drone tại relay hub trung gian.

    Yêu cầu:
    - Mission phải đang FLYING/IN_PROGRESS, handling_status = AT_HUB (đã confirm relay arrival).
    - Pin mới phải: tại đúng relay hub, sạc 100%, status ACTIVE, chưa gắn drone khác, model tương thích.

    Actions:
    - Tháo pin cũ: battery.drone_id = None, battery.current_hub_id = relay_hub_id.
    - Gán pin mới: battery.drone_id = mission.drone_id, mission.battery_id = new_battery_id.
    - Cập nhật leg tiếp theo: battery_id = new_battery_id.
    - Ghi MissionBatterySwapHistory.
    - mission.handling_status → READY (sẵn sàng dispatch).
    """
    from sqlalchemy import and_
    from app.modules.missions.models import MissionLeg, MissionLegStatus, MissionBatterySwapHistory
    from app.modules.fleet.models import DroneStatus, batteries_status
    from app.modules.missions.technician.router import get_compatible_battery_models

    mission = await _resolve_mission(db, mission_id)
    if mission.handling_status not in [HandlingStatus.AT_HUB, HandlingStatus.READY]:
        raise HTTPException(
            status_code=400,
            detail=f"Mission phải ở trạng thái AT_HUB (đã confirm relay arrival) trước khi swap pin. Hiện tại: {mission.handling_status}."
        )

    # Lấy relay hub hiện tại từ drone hoặc leg tiếp theo
    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
    if not drone:
        raise HTTPException(status_code=400, detail="Mission chưa được gán drone.")

    next_leg_result = await db.execute(
        select(MissionLeg)
        .where(
            MissionLeg.mission_id == mission.id,
            MissionLeg.status == MissionLegStatus.PENDING,
        )
        .order_by(MissionLeg.sequence_no)
        .limit(1)
    )
    next_leg = next_leg_result.scalars().first()

    relay_hub_id = drone.current_hub_id or (next_leg.from_hub_id if next_leg else None)
    if not relay_hub_id and user.hub_id and str(user.hub_id).isdigit():
        relay_hub_id = int(user.hub_id)
    if not relay_hub_id:
        raise HTTPException(status_code=400, detail="Không xác định được relay hub hiện tại của drone.")

    compatible_models = await get_compatible_battery_models(db, drone.model)

    active_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.IN_PROGRESS,
        MissionStatus.APPROVED,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
    ]
    # Tập các mã pin đã được giữ bởi các Mission active KHÁC (gồm cả Mission.battery_id và MissionLeg.battery_id)
    other_mission_bats_res = await db.execute(
        select(Mission.battery_id).where(
            and_(
                Mission.id != mission.id,
                Mission.status.in_(active_statuses),
                Mission.battery_id.is_not(None),
            )
        )
    )
    other_reserved_bat_ids = {str(r[0]) for r in other_mission_bats_res.all() if r[0]}

    other_leg_bats_res = await db.execute(
        select(MissionLeg.battery_id)
        .join(Mission, MissionLeg.mission_id == Mission.id)
        .where(
            and_(
                Mission.id != mission.id,
                Mission.status.in_(active_statuses),
                MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
                MissionLeg.battery_id.is_not(None),
            )
        )
    )
    for r in other_leg_bats_res.all():
        if r[0]:
            other_reserved_bat_ids.add(str(r[0]))

    target_battery_id = None
    if body:
        target_battery_id = (body.new_battery_id or body.battery_id or "").strip() or None

    if target_battery_id:
        new_battery = await db.get(Battery, target_battery_id)
        if not new_battery:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy pin '{target_battery_id}'.")

        if new_battery.current_hub_id != relay_hub_id:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {new_battery.id} không thuộc relay hub hiện tại (Hub {relay_hub_id})."
            )
        if new_battery.status != batteries_status.ACTIVE:
            raise HTTPException(status_code=400, detail=f"Pin {new_battery.id} không ở trạng thái Active.")
        if (new_battery.charge_level_pct or 0) <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {new_battery.id} đã cạn năng lượng (hiện {new_battery.charge_level_pct}%)."
            )
        if new_battery.drone_id is not None and new_battery.drone_id != mission.drone_id:
            raise HTTPException(status_code=400, detail=f"Pin {new_battery.id} đang gắn trên drone khác.")
        if compatible_models and new_battery.battery_model and new_battery.battery_model not in compatible_models:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {new_battery.id} (model: {new_battery.battery_model}) không tương thích với {drone.model}. Cần: {', '.join(compatible_models)}."
            )
    else:
        # Ưu tiên viên pin đã được AI / Operator chỉ định sẵn cho chặng kế tiếp (next_leg.battery_id)
        new_battery = None
        if next_leg and next_leg.battery_id:
            designated_bat = await db.get(Battery, str(next_leg.battery_id))
            if (
                designated_bat
                and designated_bat.current_hub_id == relay_hub_id
                and designated_bat.status == batteries_status.ACTIVE
                and (designated_bat.charge_level_pct or 0) > 0
                and (designated_bat.drone_id is None or designated_bat.drone_id == mission.drone_id)
                and (not compatible_models or not designated_bat.battery_model or designated_bat.battery_model in compatible_models)
                and designated_bat.id not in other_reserved_bat_ids
            ):
                new_battery = designated_bat

        # Nếu pin chỉ định trước không khả dụng hoặc chưa có, tự động chọn pin có năng lượng cao nhất tương thích rảnh tại relay hub
        if not new_battery:
            auto_stmt = select(Battery).where(
                and_(
                    Battery.current_hub_id == relay_hub_id,
                    Battery.battery_model.in_(compatible_models),
                    Battery.charge_level_pct > 0,
                    Battery.status == batteries_status.ACTIVE,
                    Battery.drone_id.is_(None),
                )
            ).order_by(Battery.charge_level_pct.desc(), Battery.capacity_wh.desc())
            res_auto = await db.execute(auto_stmt)
            candidates = [b for b in res_auto.scalars().all() if b.id not in other_reserved_bat_ids]
            new_battery = candidates[0] if candidates else None

        if not new_battery:
            raise HTTPException(
                status_code=400,
                detail=f"Tại Relay Hub #{relay_hub_id} hiện không có viên pin khả dụng nào tương thích với {drone.model}."
            )

    # Kiểm tra pin mới chưa bị giữ bởi Mission active khác (hoặc chặng trung gian của Mission khác)
    if new_battery.id in other_reserved_bat_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Pin {new_battery.id} đang được giữ chỗ bởi một nhiệm vụ bay đang hoạt động khác."
        )

    now = get_vn_naive_datetime()
    old_battery_id = mission.battery_id

    # Tháo pin cũ
    if old_battery_id and old_battery_id != new_battery.id:
        old_battery = await db.get(Battery, old_battery_id)
        if old_battery:
            old_battery.drone_id = None
            old_battery.current_hub_id = relay_hub_id  # pin cũ ở lại relay hub để sạc
            old_battery.status = batteries_status.ACTIVE  # reset về ACTIVE để cronjob sạc nhận ra

    # Gán pin mới
    new_battery.drone_id = mission.drone_id
    new_battery.current_hub_id = relay_hub_id
    mission.battery_id = new_battery.id

    # Cập nhật battery_id cho leg tiếp theo (PENDING)
    if next_leg:
        next_leg.battery_id = new_battery.id

    # Ghi lịch sử swap
    swap_record = MissionBatterySwapHistory(
        mission_id=mission.id,
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
        "mission_id": mission.id,
        "order_id": mission.order_id,
        "relay_hub_id": relay_hub_id,
        "old_battery_id": old_battery_id,
        "new_battery_id": new_battery.id,
        "new_battery_serial": new_battery.serial_number,
        "new_battery_model": new_battery.battery_model,
        "handling_status": mission.handling_status.value,
        "status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
    }


@router.post("/missions/{mission_id}/ready", summary="Technician xác nhận drone đã sẵn sàng bay")
async def mark_mission_ready(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician xác nhận đã kiểm tra drone và lắp pin xong — sẵn sàng bay.

    Yêu cầu:
    - handling_status = AT_HUB (drone đang ở hub, chờ chuẩn bị).
    - Mission phải có drone_id và battery_id.

    Actions:
    - mission.handling_status → READY.
    """
    mission = await _resolve_mission(db, mission_id)

    if mission.handling_status != HandlingStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail=f"Mission phải ở trạng thái AT_HUB mới có thể mark READY. Hiện tại: {mission.handling_status}."
        )

    if not mission.drone_id:
        raise HTTPException(status_code=400, detail="Mission chưa có drone. Operator cần phân công drone trước.")

    if not mission.battery_id:
        raise HTTPException(status_code=400, detail="Mission chưa có pin. Vui lòng lắp pin trước khi mark READY.")

    mission.handling_status = HandlingStatus.READY
    await db.commit()

    return {
        "message": "Mission đã sẵn sàng bay. Technician có thể bấm Dispatch.",
        "mission_id": mission.id,
        "handling_status": mission.handling_status.value,
        "drone_id": mission.drone_id,
        "battery_id": mission.battery_id,
    }


@router.post("/missions/{mission_id}/dispatch", summary="Dispatch drone bay tiếp chặng kế tiếp")
async def dispatch_drone(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician dispatch drone bay tiếp chặng kế tiếp sau khi swap pin tại relay hub (hoặc xuất phát từ origin hub).
    Technician chủ động cho drone bay tiếp, không cần chờ Operator phê duyệt lại (Issue #3).

    Yêu cầu:
    - handling_status = READY (đã chuẩn bị / thay pin xong).

    Actions:
    - Cập nhật mission.status → FLYING ("Flying") (khớp với bảng status FE và live-tracking).
    - Leg tiếp theo (PENDING) → IN_PROGRESS, started_at = now.
    - mission.handling_status → INCOMING (drone đang bay, không còn ở hub).
    - Drone.status → In Flight, drone.current_hub_id = None.
    - Tạo thông báo (Notification) gửi trực tiếp cho tất cả Operators để tiếp tục theo dõi chuyến bay.
    """
    from app.modules.missions.models import (
        MissionLeg,
        MissionLegStatus,
        Order as OrderModel,
        OrderStatus,
    )
    from app.modules.system.models import Notification, NotificationType
    from app.modules.fleet.models import DroneStatus
    from app.shared.workers import get_operator_ids

    mission = await _resolve_mission(db, mission_id)
    if mission.handling_status != HandlingStatus.READY:
        raise HTTPException(
            status_code=400,
            detail=f"Mission phải ở trạng thái READY (đã lắp/thay pin) trước khi dispatch. Hiện tại: {mission.handling_status}."
        )

    now = get_vn_naive_datetime()

    # Tìm leg tiếp theo cần bay (PENDING)
    next_leg_result = await db.execute(
        select(MissionLeg)
        .where(
            MissionLeg.mission_id == mission.id,
            MissionLeg.status == MissionLegStatus.PENDING,
        )
        .order_by(MissionLeg.sequence_no)
        .limit(1)
    )
    next_leg = next_leg_result.scalars().first()

    # Nếu chưa có leg PENDING nhưng có leg đang IN_PROGRESS (trường hợp re-dispatch), dùng leg đó
    if not next_leg:
        inprog_leg_res = await db.execute(
            select(MissionLeg)
            .where(
                MissionLeg.mission_id == mission.id,
                MissionLeg.status == MissionLegStatus.IN_PROGRESS,
            )
            .order_by(MissionLeg.sequence_no)
            .limit(1)
        )
        next_leg = inprog_leg_res.scalars().first()

    # Nếu mission legacy chưa có bản ghi MissionLeg nào thì tự khởi tạo leg 1
    if not next_leg:
        all_legs_res = await db.execute(
            select(MissionLeg).where(MissionLeg.mission_id == mission.id)
        )
        if not all_legs_res.scalars().all() and mission.origin_hub_id and mission.destination_hub_id:
            next_leg = MissionLeg(
                mission_id=mission.id,
                sequence_no=1,
                from_hub_id=mission.origin_hub_id,
                to_hub_id=mission.destination_hub_id,
                battery_id=mission.battery_id,
                status=MissionLegStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            db.add(next_leg)
            await db.flush()

    if not next_leg:
        raise HTTPException(
            status_code=400,
            detail="Không còn chặng bay nào ở trạng thái PENDING. Tất cả legs đã hoàn thành?"
        )

    # Chuyển leg sang IN_PROGRESS
    next_leg.status = MissionLegStatus.IN_PROGRESS
    next_leg.started_at = now
    if mission.battery_id and not next_leg.battery_id:
        next_leg.battery_id = mission.battery_id

    # Cập nhật trạng thái Mission sang FLYING ("Flying") khớp với bảng flow
    mission.status = MissionStatus.FLYING
    if not mission.start_time:
        mission.start_time = now
    if not mission.departed_at:
        mission.departed_at = now
    mission.handling_status = HandlingStatus.INCOMING

    # Nếu Order đang ở AT_HUB, chuyển sang IN_DELIVERY khi drone cất cánh
    if mission.order_id:
        order_obj = await db.get(OrderModel, mission.order_id)
        if order_obj and order_obj.status == OrderStatus.AT_HUB:
            order_obj.status = OrderStatus.IN_DELIVERY
            order_obj.updated_at = now

    # Drone cất cánh → In Flight, rời hub
    if mission.drone_id:
        drone = await db.get(Drone, mission.drone_id)
        if drone:
            drone.status = DroneStatus.IN_FLIGHT
            drone.current_hub_id = None

    # Pin cất cánh cùng drone → rời Battery Management (current_hub_id = None)
    if mission.battery_id:
        battery = await db.get(Battery, mission.battery_id)
        if battery:
            battery.current_hub_id = None
            battery.status = "In Use"

    # Thông tin leg đang bay và leg tiếp theo (nếu có)
    remaining_legs_result = await db.execute(
        select(MissionLeg)
        .where(
            MissionLeg.mission_id == mission.id,
            MissionLeg.status == MissionLegStatus.PENDING,
        )
        .order_by(MissionLeg.sequence_no)
    )
    remaining = remaining_legs_result.scalars().all()

    from app.modules.system.models import Hub as HubModel
    from_hub = await db.get(HubModel, next_leg.from_hub_id) if next_leg.from_hub_id else None
    to_hub = await db.get(HubModel, next_leg.to_hub_id) if next_leg.to_hub_id else None
    from_hub_label = from_hub.name if from_hub else f"Hub #{next_leg.from_hub_id}"
    to_hub_label = to_hub.name if to_hub else f"Hub #{next_leg.to_hub_id}"
    total_legs = next_leg.sequence_no + len(remaining)

    notify_msg = (
        f"Technician đã Dispatch Drone #{mission.drone_id} cho nhiệm vụ {mission.id} "
        f"(Đơn: {mission.order_id or 'N/A'}): bay chặng {next_leg.sequence_no}/{total_legs} "
        f"từ {from_hub_label} → {to_hub_label}."
    )

    # Gửi Notification thực tế vào Database cho tất cả Operators (Issue #3)
    try:
        operator_ids = await get_operator_ids(db)
        for op_id in operator_ids:
            db.add(Notification(
                user_id=op_id,
                title=f"Drone #{mission.drone_id} đã Dispatch chặng {next_leg.sequence_no}/{total_legs}",
                message=notify_msg,
                notifications_type=NotificationType.MISSION_STATUS,
                mission_id=str(mission.id),
                reference_id="TECHNICIAN_DISPATCH",
                created_at=now,
            ))
    except Exception as e:
        print(f"[NOTIFICATION] Lỗi gửi thông báo dispatch cho Operator: {e}")

    await db.commit()

    # Spawn background task giả lập drone bay theo leg vừa dispatch
    try:
        from app.shared.drone_simulator import simulate_mission_flight
        asyncio.create_task(simulate_mission_flight(str(mission.id)))
        print(f"[DISPATCH] Spawned simulator task cho mission {mission.id}")
    except Exception as e:
        print(f"[DISPATCH] Lỗi spawn simulator: {e}")

    return {
        "message": f"Drone đã được dispatch. Bay tiếp chặng {next_leg.sequence_no} → {to_hub_label}.",
        "mission_id": mission.id,
        "order_id": mission.order_id,
        "status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        "handling_status": mission.handling_status.value,
        "dispatched_leg": {
            "id": next_leg.id,
            "sequence_no": next_leg.sequence_no,
            "from_hub_id": next_leg.from_hub_id,
            "to_hub_id": next_leg.to_hub_id,
            "status": next_leg.status.value,
            "started_at": now.isoformat(),
        },
        "remaining_legs": len(remaining),
        "notify_operator": notify_msg,
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
