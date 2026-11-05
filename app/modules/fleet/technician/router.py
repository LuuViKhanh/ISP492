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
    Drone, Battery, WorkOrder, WorkOrderStatus, batteries_status,
    MaintenanceAlert, MaintenanceSchedule, DroneStatus,
    PartCategory, PartsRequest, PartsRequestStatus, HubInventory, PartCategoryType
)
from app.modules.system.models import Hub
from app.modules.fleet.schemas import WorkOrderCreate, WorkOrderUpdate, InspectionUpdate, WorkOrderResponse
from app.modules.fleet.technician.schemas import (
    DroneProfileResponse, DroneStatusUpdate, MaintenanceHistoryItem,
    MaintenanceRecordCreate, MaintenanceRecordResponse,
    WorkOrderLogResponse, MaintenanceAlertResponse,
    MaintenanceScheduleResponse, MaintenanceScheduleCreate,
    IncomingMissionResponse, BatteryResponse, BatteryUseRequest,
    PartCategoryCreate, PartCategoryUpdate, PartCategoryResponse,
    PartsRequestCreate, PartsRequestApprove, PartsRequestReject, PartsRequestResponse,
    HubInventoryCreate, HubInventoryUpdate, HubInventoryResponse, HubInventoryDetailedResponse
)
from app.modules.missions.models import Mission, MissionStatus, HandlingStatus, Incident, IncidentStatus
from app.core.config import settings

router = APIRouter(prefix="/technician/fleet", tags=["Fleet"])

allow_technician = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])
allow_admin = RoleChecker([UserRole.ADMIN])
allow_admin_or_operator = RoleChecker([UserRole.ADMIN, UserRole.OPERATOR])


from typing import Optional, List
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
    from app.modules.missions.models import MissionLeg, MissionLegStatus
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
                    MissionLeg.to_hub_id == tech_hub_id,
                    MissionLeg.status == MissionLegStatus.IN_PROGRESS,
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
    tech_hub_id = int(user.hub_id) if (user.hub_id and str(user.hub_id).isdigit()) else None

    # Kiểm tra có incident OPEN hoặc INVESTIGATING không, hoặc Technician báo lỗi
    incident_result = await db.execute(
        select(Incident).where(
            Incident.mission_id == mission.id,
            Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.INVESTIGATING])
        ).order_by(Incident.id.desc()).limit(1)
    )
    open_incident = incident_result.scalars().first()
    has_open_incident = open_incident is not None

    has_issue_flag = False
    if body:
        if body.has_issue or body.has_incident:
            has_issue_flag = True
        if body.drone_status and str(body.drone_status).strip().lower() == "maintenance":
            has_issue_flag = True

    is_incident_flight = (
        mission.status == MissionStatus.INCIDENT_RETURN
        or mission.handling_status == HandlingStatus.CANNOT_CONTINUE
        or has_open_incident
        or has_issue_flag
    )

    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None

    # ── Xác định Hub tiếp nhận thực tế (receiving_hub_id) ───────────────────
    # 1. Technician đăng nhập tại Hub nào thì tiếp nhận drone tại Hub đó (tech_hub_id)
    # 2. Nếu không có tech_hub_id: nếu chuyến bay sự cố quay về thì tiếp nhận tại drone.current_hub_id hoặc mission.origin_hub_id; nếu bay bình thường thì destination_hub_id
    if tech_hub_id is not None:
        receiving_hub_id = tech_hub_id
    elif is_incident_flight:
        receiving_hub_id = (drone.current_hub_id if drone else None) or mission.origin_hub_id or mission.destination_hub_id
    else:
        receiving_hub_id = mission.destination_hub_id

    # ── 1. Cập nhật Mission (rời danh sách Transit đang hoạt động sang History) ──
    if not mission.arrived_at:
        mission.arrived_at = now       # ghi nhận thời điểm drone đáp hub
    if mission.status != MissionStatus.MISSION_COMPLETED:
        mission.status = MissionStatus.COMPLETED
    mission.end_time = mission.end_time or now
    mission.handling_status = None     # xóa handling_status để không còn nằm trong /incoming (Transit)

    # Nếu là chuyến bay sự cố, drone quay về Hub tiếp nhận và kết thúc chặng bay này.
    # Ghi nhận arrival_confirmed_at để không treo ở hàng đợi chờ khách nhận hàng (/confirmations)
    # và không kích hoạt pickup_reminder_watcher hủy đơn nhầm.
    if is_incident_flight:
        mission.arrival_confirmed_at = now
        mission.arrival_confirmed_by = user.id

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
    if drone:
        if receiving_hub_id:
            drone.current_hub_id = receiving_hub_id

        drone.status = DroneStatus.MAINTENANCE if (has_open_incident or has_issue_flag) else DroneStatus.AVAILABLE

        # Đảm bảo có MaintenanceAlert ở trạng thái Pending khi drone chuyển sang Maintenance
        if drone.status == DroneStatus.MAINTENANCE:
            from app.modules.fleet.models import MaintenanceAlert
            existing_alert_res = await db.execute(
                select(MaintenanceAlert).where(
                    MaintenanceAlert.drone_id == drone.id,
                    MaintenanceAlert.work_order_id.is_(None),
                    MaintenanceAlert.status.in_(["Pending", "PENDING"])
                ).limit(1)
            )
            existing_alert = existing_alert_res.scalars().first()
            if not existing_alert:
                issue_detail = ""
                if body and body.issue_description:
                    issue_detail = f": {body.issue_description.strip()}"
                elif open_incident and open_incident.description:
                    issue_detail = f": {open_incident.description.strip()}"

                alert_title = f"Sự cố chuyến bay #{mission.id} — Drone #{drone.id} cần kiểm tra kỹ thuật{issue_detail[:60]}"
                new_alert = MaintenanceAlert(
                    drone_id=drone.id,
                    mission_id=str(mission.id),
                    incident_id=open_incident.id if open_incident else None,
                    source="Incident" if is_incident_flight else "Inspection",
                    title=alert_title,
                    status="Pending",
                    created_at=now,
                )
                db.add(new_alert)

    # ── 4. Battery: tháo khỏi drone, chuyển sang Battery Management tại Hub tiếp nhận ──
    released_battery_id = mission.battery_id
    if mission.battery_id:
        battery = await db.get(Battery, mission.battery_id)
        if battery:
            battery.drone_id = None
            if receiving_hub_id:
                battery.current_hub_id = receiving_hub_id
            if body and body.battery_faulty:
                battery.status = batteries_status.REPLACED

    # Đảm bảo bất kỳ viên pin nào khác đang gắn trên drone này cũng được tháo ra đưa về Battery Management
    if mission.drone_id:
        attached_bats_res = await db.execute(
            select(Battery).where(Battery.drone_id == mission.drone_id)
        )
        for b in attached_bats_res.scalars().all():
            b.drone_id = None
            if receiving_hub_id:
                b.current_hub_id = receiving_hub_id
            if body and body.battery_faulty:
                b.status = batteries_status.REPLACED
            if not released_battery_id:
                released_battery_id = b.id

    # ── 5. Order: DELIVERED_TO_HUB (nếu bình thường) hoặc AT_HUB + replan_required_at (nếu sự cố) ──
    try:
        if mission.order_id:
            order_result = await db.execute(
                select(OrderModel).where(OrderModel.id == mission.order_id)
            )
            order_obj = order_result.scalar_one_or_none()
            if order_obj:
                hub_name = None
                if receiving_hub_id:
                    hub = await db.get(HubModel, receiving_hub_id)
                    hub_name = hub.name if hub else None

                if is_incident_flight:
                    # Kiện hàng quay lại Hub gốc do sự cố chuyến bay
                    order_obj.status = OrderStatus.AT_HUB
                    order_obj.replan_required_at = now
                    order_obj.updated_at = now

                    # Thông báo cho Customer: đơn hàng tạm hoãn do sự cố kỹ thuật và đang được sắp xếp lại
                    if order_obj.customer_id:
                        from app.modules.system.models import Notification as _Notif, NotificationType as _NT
                        db.add(_Notif(
                            user_id=order_obj.customer_id,
                            title="Đơn hàng tạm hoãn do sự cố kỹ thuật",
                            message=(
                                f"Đơn hàng #{order_obj.id} gặp sự cố trong quá trình bay và đã được đưa về "
                                f"{hub_name or 'Hub an toàn'}. Hệ thống đang sắp xếp chuyến bay thay thế."
                            ),
                            notifications_type=_NT.MISSION_STATUS,
                            reference_id=f"DRONE_INCIDENT_{mission.id}",
                            mission_id=str(mission.id),
                            created_at=now,
                        ))
                else:
                    # Chuyến bay đáp hub đích thành công
                    order_obj.status = OrderStatus.DELIVERED_TO_HUB
                    order_obj.destination_received_at = now
                    order_obj.destination_received_by = str(user.id)
                    order_obj.updated_at = now

                    if order_obj.receiver_email:
                        await send_hub_arrival_email(
                            to_email=order_obj.receiver_email,
                            order_id=order_obj.id,
                            hub_name=hub_name,
                            frontend_url=settings.FRONTEND_URL,
                        )

                    # ── Notify Customer: drone đã tới Hub đích ───────────────────
                    if order_obj.customer_id:
                        from app.modules.system.models import Notification as _Notif, NotificationType as _NT
                        db.add(_Notif(
                            user_id=order_obj.customer_id,
                            title="🚁 Drone đã tới Hub — Hàng của bạn đã về!",
                            message=(
                                f"Đơn hàng #{order_obj.id} của bạn đã được drone vận chuyển tới "
                                f"{hub_name or 'Hub đích'}. "
                                f"Vui lòng tới nhận hàng sớm nhất có thể. "
                                f"Hàng sẽ được lưu tối đa 3 ngày."
                            ),
                            notifications_type=_NT.MISSION_STATUS,
                            reference_id=f"DRONE_ARRIVED_{mission.id}",
                            mission_id=str(mission.id),
                            created_at=now,
                        ))
    except Exception as e:
        print(f"[ORDER_CONFIRM] Lỗi cập nhật đơn hàng: {e}")

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
        "hub_id": receiving_hub_id,
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


@router.get("/drones/{drone_id}/flight-history", response_model=list, summary="Lịch bay của drone")
async def get_drone_flight_history(
    drone_id: str,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy lịch bay của drone (Flight History).
    
    - TECHNICIAN: chỉ xem drone tại hub của mình
    - Trả về danh sách missions, sorted by arrived_at DESC (gần nhất trước)
    - Mỗi mission có field `is_relay_mission` để phân biệt chuyến bay thường vs chuyến bay trung gian
    """
    from app.modules.missions.models import Mission, Order as OrderModel, MissionLeg
    
    # Kiểm tra drone có tại hub của technician không
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    if user.hub_id and drone.current_hub_id != int(user.hub_id):
        raise HTTPException(status_code=403, detail="Drone không ở Hub của bạn")
    
    # Lấy flight history
    query = (
        select(
            Mission.id.label("mission_id"),
            Mission.status.label("mission_status"),
            Mission.started_at,
            Mission.arrived_at,
            Mission.battery_id,
            OrderModel.id.label("order_id"),
            OrderModel.order_code,
        )
        .outerjoin(OrderModel, Mission.order_id == OrderModel.id)
        .where(
            Mission.drone_id == drone_id,
            Mission.status == MissionStatus.COMPLETED
        )
        .order_by(Mission.arrived_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(query)
    flights = []
    for row in result.fetchall():
        duration_min = None
        if row.started_at and row.arrived_at:
            duration = row.arrived_at - row.started_at
            duration_min = int(duration.total_seconds() / 60)
        
        # Kiểm tra số lượng legs để xác định chuyến bay trung gian
        legs_count_res = await db.execute(
            select(MissionLeg).where(MissionLeg.mission_id == row.mission_id)
        )
        legs_count = len(legs_count_res.scalars().all())
        is_relay_mission = legs_count > 1  # Nếu > 1 leg → là chuyến bay trung gian
        
        flights.append({
            "mission_id": row.mission_id,
            "order_id": row.order_id,
            "order_code": row.order_code,
            "started_at": row.started_at,
            "arrived_at": row.arrived_at,
            "duration_minutes": duration_min,
            "battery_id": row.battery_id,
            "is_relay_mission": is_relay_mission,
            "legs_count": legs_count,
        })
    
    return flights


@router.get("/drones/{drone_id}/maintenance-history", response_model=list, summary="Lịch bảo trì của drone")
async def get_drone_maintenance_history(
    drone_id: str,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy lịch bảo trì của drone (Maintenance History).
    
    - TECHNICIAN: chỉ xem drone tại hub của mình
    - Trả về danh sách work orders + maintenance records, sorted by resolved_at DESC (gần nhất trước)
    """
    # Kiểm tra drone có tại hub của technician không
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    if user.hub_id and drone.current_hub_id != int(user.hub_id):
        raise HTTPException(status_code=403, detail="Drone không ở Hub của bạn")
    
    # Lấy maintenance history từ bảng work_orders đã hợp nhất
    query = (
        select(WorkOrder)
        .where(WorkOrder.drone_id == drone_id)
        .order_by(WorkOrder.resolved_at.desc().nullslast())
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(query)
    work_orders = result.scalars().all()
    maintenance_records = []
    for wo in work_orders:
        maintenance_records.append({
            "work_order_id": wo.id,
            "status": wo.status.value if wo.status else None,
            "issue_description": wo.issue_description,
            "action_taken": wo.action_taken,
            "resolved_at": wo.resolved_at,
            "maintenance_type": "Scheduled" if wo.scheduled_at else "Corrective",
            "record_description": wo.diagnosis or wo.issue_description,
        })
    
    return maintenance_records


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
    
    # Nếu cố gắng update status từ MAINTENANCE thành AVAILABLE → kiểm tra có alert pending không
    if drone.status == DroneStatus.MAINTENANCE and body.status == DroneStatus.AVAILABLE:
        pending_alerts_result = await db.execute(
            select(MaintenanceAlert).where(
                MaintenanceAlert.drone_id == drone_id,
                MaintenanceAlert.work_order_id == None,  # alert chưa được xử lý (chưa có work order)
                MaintenanceAlert.status == "Pending"
            ).limit(1)
        )
        if pending_alerts_result.scalars().first():
            raise HTTPException(
                status_code=400,
                detail="Không thể chuyển drone về Available khi còn Maintenance Alert Pending chưa xử lý. Vui lòng giải quyết alert trước."
            )
    
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

    # Import helper tính vòng đời & trạng thái khả dụng pin (on-the-fly, không lưu DB)
    from app.modules.fleet.models import compute_battery_lifecycle, compute_battery_available_status

    response = []
    for b in batteries:
        avail_status = compute_battery_available_status(b)
        avail_str = avail_status.value if hasattr(avail_status, "value") else str(avail_status)
        mins = get_estimated_charging_minutes(b.charge_level_pct) if avail_str == "Charging" else 0
        lifecycle_status, days_until_expiry = compute_battery_lifecycle(b)
        response.append({
            "id": b.id,
            "serial_number": b.serial_number,
            "battery_model": b.battery_model,
            "capacity_wh": b.capacity_wh,
            "status": b.status,
            "drone_id": b.drone_id,
            "current_hub_id": b.current_hub_id,
            "charge_level_pct": b.charge_level_pct,
            "create_at": b.create_at,
            "update_at": b.update_at,
            "last_charged_at": b.last_charged_at,
            "estimated_minutes_remaining": mins,
            "available_status": avail_str,
            "lifecycle_status": lifecycle_status.value if hasattr(lifecycle_status, "value") else str(lifecycle_status),
            "days_until_expiry": days_until_expiry,
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
    battery.status = batteries_status.ACTIVE
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
    battery.last_charged_at = get_vn_naive_datetime()
    await db.commit()
    await db.refresh(battery)
    
    return {
        "message": "Đã cập nhật pin sạc đầy 100%",
        "battery": {
            "id": battery.id,
            "serial_number": battery.serial_number,
            "charge_level_pct": battery.charge_level_pct,
            "current_hub_id": battery.current_hub_id,
            "last_charged_at": battery.last_charged_at.isoformat() if battery.last_charged_at else None,
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
        select(WorkOrder)
        .where(
            WorkOrder.drone_id == drone_id,
            (WorkOrder.diagnosis.isnot(None)) | (WorkOrder.action_taken.isnot(None))
        )
        .order_by(WorkOrder.id.desc())
    )
    work_orders = result.scalars().all()
    response = []
    for wo in work_orders:
        response.append(MaintenanceRecordResponse(
            id=wo.id,
            work_order_id=wo.id,
            drone_id=wo.drone_id,
            performed_by=wo.technician_id,
            title=wo.title,
            diagnosis=wo.diagnosis,
            corrective_action=wo.action_taken,
            resulting_drone_status=wo.resulting_drone_status,
            created_at=wo.started_at or wo.scheduled_at,
            completed_at=wo.record_completed_at or wo.resolved_at or wo.completed_at,
            inspection_items=wo.inspection_items_json or [],
        ))
    return response


@router.post("/maintenance-records", response_model=MaintenanceRecordResponse, status_code=201)
async def create_maintenance_record(
    body: MaintenanceRecordCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = await db.get(WorkOrder, body.work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    now = get_vn_naive_datetime()
    wo.diagnosis = body.diagnosis
    wo.action_taken = body.corrective_action or wo.action_taken
    wo.resulting_drone_status = body.resulting_drone_status
    wo.record_completed_at = now
    if body.title:
        wo.title = body.title

    items = []
    for idx, item in enumerate(body.inspection_items or [], 1):
        items.append({
            "id": idx,
            "item_name": item.item_name,
            "is_completed": item.is_completed,
            "checked_at": now.isoformat() if item.is_completed else None,
        })
    wo.inspection_items_json = items

    await db.commit()
    await db.refresh(wo)

    return MaintenanceRecordResponse(
        id=wo.id,
        work_order_id=wo.id,
        drone_id=wo.drone_id,
        performed_by=user.id,
        title=wo.title,
        diagnosis=wo.diagnosis,
        corrective_action=wo.action_taken,
        resulting_drone_status=wo.resulting_drone_status,
        created_at=now,
        completed_at=wo.record_completed_at,
        inspection_items=items,
    )


# ── Work Order Logs ───────────────────────────────────────────────────────────

@router.get("/work-orders/{work_order_id}/logs", response_model=list[WorkOrderLogResponse])
async def get_work_order_logs(
    work_order_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    wo = await db.get(WorkOrder, work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    logs = list(reversed(wo.status_logs_json or []))
    return logs


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
    """Chỉ trả alerts chưa được xử lý (chưa có work order) tại hub của technician."""
    query = (
        select(MaintenanceAlert)
        .outerjoin(Drone, MaintenanceAlert.drone_id == Drone.id)
        .where(MaintenanceAlert.work_order_id == None)
        .order_by(MaintenanceAlert.id.desc())
    )
    
    # TECHNICIAN chỉ thấy alert của drone tại hub mình
    if user.hub_id and str(user.hub_id).isdigit():
        query = query.where(Drone.current_hub_id == int(user.hub_id))
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/maintenance-alerts/history", response_model=list[MaintenanceAlertResponse])
async def list_maintenance_alerts_history(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """Trả alerts đã được xử lý (đã có work order) tại hub của technician."""
    query = (
        select(MaintenanceAlert)
        .outerjoin(Drone, MaintenanceAlert.drone_id == Drone.id)
        .where(MaintenanceAlert.work_order_id != None)
        .order_by(MaintenanceAlert.id.desc())
    )
    
    # TECHNICIAN chỉ thấy alert của drone tại hub mình
    if user.hub_id and str(user.hub_id).isdigit():
        query = query.where(Drone.current_hub_id == int(user.hub_id))
    
    result = await db.execute(query)
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
    from app.core.timezone import get_vn_naive_datetime
    
    wo = WorkOrder(
        drone_id=body.drone_id,
        battery_id=body.battery_id,
        technician_id=user.id,
        issue_description=body.issue_description,
        status=WorkOrderStatus.PENDING,
        created_at=get_vn_naive_datetime(),
    )
    db.add(wo)
    await db.flush()  # lấy wo.id trước khi commit

    # Cập nhật drone sang MAINTENANCE khi tạo work order (drone đi bảo trì)
    if body.drone_id:
        drone = await db.get(Drone, body.drone_id)
        if drone:
            drone.status = DroneStatus.MAINTENANCE
    
    # Cập nhật battery: set work_order_id để tracking pin đang dùng
    if body.battery_id:
        battery = await db.get(Battery, body.battery_id)
        if battery:
            battery.work_order_id = wo.id

    # Nếu tạo từ một alert → gắn work_order_id vào alert, chuyển alert sang history
    if body.alert_id:
        alert = await db.get(MaintenanceAlert, body.alert_id)
        if alert:
            alert.work_order_id = wo.id
    else:
        # Nếu không có alert_id, tự động tìm alert pending của drone này để gắn vào
        if body.drone_id:
            alert_res = await db.execute(
                select(MaintenanceAlert)
                .where(
                    MaintenanceAlert.drone_id == body.drone_id,
                    MaintenanceAlert.work_order_id == None,
                    MaintenanceAlert.status.in_(["Pending", "PENDING", "Acknowledged"])
                )
                .order_by(MaintenanceAlert.id.desc())
                .limit(1)
            )
            alert = alert_res.scalar_one_or_none()
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

    # Khi Complete: bắt buộc phải có action_taken
    if body.status == WorkOrderStatus.COMPLETED:
        if not body.action_taken:
            raise HTTPException(
                status_code=400,
                detail="Phải cung cấp action_taken khi hoàn thành Work Order"
            )

    old_status = wo.status.value
    wo.action_taken = body.action_taken
    wo.status = body.status
    if body.diagnosis:
        wo.diagnosis = body.diagnosis
    if body.resulting_drone_status:
        wo.resulting_drone_status = body.resulting_drone_status
    now = get_vn_naive_datetime()
    
    if body.status == WorkOrderStatus.COMPLETED:
        wo.resolved_at = now
        
        # Clear work_order_id từ battery (pin không còn dùng)
        if wo.battery_id:
            battery = await db.get(Battery, wo.battery_id)
            if battery:
                battery.work_order_id = None
        
        # ── Fix: Cập nhật maintenance schedule khi work order hoàn thành ──
        # Nếu work order được tạo từ 1 maintenance alert có schedule_id
        # → Cập nhật next_inspection_at để lịch bảo trì tiếp theo là +interval_days
        if wo.maintenance_schedule_id:
            schedule = await db.get(MaintenanceSchedule, wo.maintenance_schedule_id)
            if schedule and schedule.interval_days:
                schedule.last_inspection_at = now
                schedule.next_inspection_at = now + timedelta(days=schedule.interval_days)
                print(f"[MAINTENANCE] Schedule #{schedule.id} updated: next inspection {schedule.next_inspection_at}")
        
        # ── Tự động acknowledge maintenance alert liên quan ──
        # Tìm alert gắn với work order này (nếu có) và chuyển sang Acknowledged
        if wo.alert_id:
            alert = await db.get(MaintenanceAlert, wo.alert_id)
            if alert and alert.status == "Pending":
                alert.status = "Acknowledged"
                alert.acknowledged_by = user.id
                alert.acknowledged_at = now
                print(f"[MAINTENANCE] Alert #{alert.id} acknowledged by technician {user.id}")
    
    new_log = {
        "id": len(wo.status_logs_json or []) + 1,
        "work_order_id": wo.id,
        "action": "inspection_update",
        "from_status": old_status,
        "to_status": body.status.value,
        "changed_by": user.id,
        "created_at": now.isoformat() if hasattr(now, "isoformat") else str(now),
    }
    cur_logs = list(wo.status_logs_json or [])
    cur_logs.append(new_log)
    wo.status_logs_json = cur_logs
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
    from app.modules.missions.models import MissionLeg, MissionLegStatus
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

    # Ghi nhận thay pin hoàn tất

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
    
    # ── Set leg_destination_type: destination vs connection ──
    # Nếu leg tiếp theo không phải leg cuối cùng → connection (relay hub)
    # Nếu leg tiếp theo là leg cuối cùng → destination (đích cuối)
    total_legs_res = await db.execute(
        select(MissionLeg).where(MissionLeg.mission_id == mission.id)
    )
    total_legs = len(total_legs_res.scalars().all())
    
    if next_leg.sequence_no < total_legs:
        mission.leg_destination_type = "connection"  # Bay đến relay hub
    else:
        mission.leg_destination_type = "destination"  # Bay đến destination cuối
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
            battery.status = batteries_status.ACTIVE

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


# ── Parts Management (Quản lý vật tư tại Hub) ──────────────────────────────────────
#
# Quản lý vật tư theo danh mục loại (PartCategory) và tồn kho tại mỗi Hub (HubInventory).
# Yêu cầu cấp phát vật tư từ Technician (PartsRequest).
#
# Phân quyền:
# - TECHNICIAN: quản lý tồn kho tại Hub của mình, yêu cầu cấp phát vật tư.
# - ADMIN: quản lý danh mục vật tư (PartCategory), duyệt yêu cầu cấp phát.

# ==================== PART CATEGORY ENDPOINTS (Admin Only) ====================

@router.get("/part-categories", response_model=List[PartCategoryResponse], summary="Lấy danh sách danh mục vật tư")
async def list_part_categories(
    is_active: Optional[bool] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy danh sách danh mục vật tư (tất cả hoặc chỉ active).
    - TECHNICIAN & ADMIN: có thể xem.
    - Query param `is_active=true|false` để lọc.
    """
    query = select(PartCategory).order_by(PartCategory.id)

    if is_active is not None:
        query = query.where(PartCategory.is_active == is_active)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/part-categories/{category_id}", response_model=PartCategoryResponse, summary="Lấy chi tiết danh mục vật tư")
async def get_part_category(
    category_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """Lấy chi tiết danh mục vật tư theo ID."""
    category = await db.get(PartCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Part category not found")
    return category


@router.post("/part-categories", response_model=PartCategoryResponse, status_code=201, summary="Tạo danh mục vật tư")
async def create_part_category(
    body: PartCategoryCreate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    ADMIN ONLY: Tạo danh mục vật tư mới.
    """
    now = get_vn_naive_datetime()
    
    # Check if name already exists
    existing = await db.execute(
        select(PartCategory).where(PartCategory.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Part category name already exists")

    category = PartCategory(
        name=body.name,
        category_type=body.category_type,
        compatible_drone_models=body.compatible_drone_models,
        unit_cost=body.unit_cost,
        description=body.description,
        is_active=body.is_active,
        created_at=now,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@router.put("/part-categories/{category_id}", response_model=PartCategoryResponse, summary="Cập nhật danh mục vật tư")
async def update_part_category(
    category_id: int,
    body: PartCategoryUpdate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    ADMIN ONLY: Cập nhật danh mục vật tư (partial update).
    """
    category = await db.get(PartCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Part category not found")

    # Check name uniqueness if updating
    if body.name is not None and body.name != category.name:
        existing = await db.execute(
            select(PartCategory).where(PartCategory.name == body.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Part category name already exists")
        category.name = body.name

    if body.category_type is not None:
        category.category_type = body.category_type
    if body.compatible_drone_models is not None:
        category.compatible_drone_models = body.compatible_drone_models
    if body.unit_cost is not None:
        category.unit_cost = body.unit_cost
    if body.description is not None:
        category.description = body.description
    if body.is_active is not None:
        category.is_active = body.is_active

    await db.commit()
    await db.refresh(category)
    return category


@router.delete("/part-categories/{category_id}", status_code=204, summary="Xóa danh mục vật tư")
async def delete_part_category(
    category_id: int,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    ADMIN ONLY: Xóa danh mục vật tư (thường là soft delete bằng set is_active=False).
    Hard delete nếu chưa có parts request nào liên quan.
    """
    category = await db.get(PartCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Part category not found")

    # Check if there are related parts requests
    related_requests = await db.execute(
        select(PartsRequest).where(PartsRequest.part_category_id == category_id).limit(1)
    )
    if related_requests.scalar_one_or_none():
        # Soft delete: set is_active = False
        category.is_active = False
        await db.commit()
    else:
        # Hard delete if no related requests
        await db.delete(category)
        await db.commit()


# ==================== HUB INVENTORY ENDPOINTS ====================

@router.get("/hub-inventories", response_model=List[HubInventoryResponse], summary="Lấy danh sách tồn kho vật tư tại Hub")
async def list_hub_inventories(
    hub_id: Optional[int] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy danh sách tồn kho vật tư tại Hub.
    - TECHNICIAN: tự động lọc theo Hub của technician (bỏ qua tham số hub_id).
    - ADMIN: có thể truyền `?hub_id=` để lọc theo Hub cụ thể, không truyền thì trả tất cả.
    """
    query = select(HubInventory).order_by(HubInventory.id)

    # TECHNICIAN chỉ xem tồn kho tại Hub của mình
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id:
            return []
        query = query.where(HubInventory.hub_id == int(user.hub_id))
    elif hub_id is not None:
        # ADMIN có thể lọc theo hub_id
        query = query.where(HubInventory.hub_id == hub_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/hub-inventories/{inventory_id}", response_model=HubInventoryDetailedResponse, summary="Lấy chi tiết tồn kho")
async def get_hub_inventory(
    inventory_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy chi tiết tồn kho vật tư (bao gồm thông tin loại vật tư).
    - TECHNICIAN: chỉ xem tồn kho tại Hub của mình.
    """
    inventory = await db.get(HubInventory, inventory_id)
    if not inventory:
        raise HTTPException(status_code=404, detail="Hub inventory not found")

    # Check permission
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id or inventory.hub_id != int(user.hub_id):
            raise HTTPException(status_code=403, detail="Tồn kho không thuộc Hub của bạn")

    # Get part category
    part_category = await db.get(PartCategory, inventory.part_category_id)
    if not part_category:
        raise HTTPException(status_code=404, detail="Part category not found")

    return {
        "id": inventory.id,
        "hub_id": inventory.hub_id,
        "part_category": part_category,
        "quantity": inventory.quantity,
        "min_stock_threshold": inventory.min_stock_threshold,
        "is_low_stock": inventory.quantity < inventory.min_stock_threshold,
        "updated_at": inventory.updated_at,
    }


@router.post("/hub-inventories", response_model=HubInventoryResponse, status_code=201, summary="Tạo tồn kho vật tư")
async def create_hub_inventory(
    body: HubInventoryCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Tạo bản ghi tồn kho vật tư mới tại Hub.
    - TECHNICIAN: hub_id tự động lấy từ user.hub_id.
    - ADMIN: có thể tạo cho hub bất kỳ (nếu truyền hub_id trong headers hoặc gán qua admin interface khác).
    """
    now = get_vn_naive_datetime()

    # TECHNICIAN: tự động sử dụng hub của mình
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id:
            raise HTTPException(status_code=400, detail="Technician chưa được gán Hub nào")
        target_hub_id = int(user.hub_id)
    else:
        # ADMIN: có thể được gán hub nào đó thông qua request context hoặc default
        target_hub_id = user.hub_id if (user.hub_id and str(user.hub_id).isdigit()) else None
        if not target_hub_id:
            raise HTTPException(status_code=400, detail="Hub ID không được xác định")

    # Verify hub exists
    hub = await db.get(Hub, target_hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail=f"Hub #{target_hub_id} không tồn tại")

    # Verify part category exists
    part_category = await db.get(PartCategory, body.part_category_id)
    if not part_category:
        raise HTTPException(status_code=404, detail=f"Part category #{body.part_category_id} không tồn tại")

    # Check if inventory already exists for this hub + part
    existing = await db.execute(
        select(HubInventory).where(
            (HubInventory.hub_id == target_hub_id) &
            (HubInventory.part_category_id == body.part_category_id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Tồn kho này đã tồn tại cho Hub #{target_hub_id} và Part Category #{body.part_category_id}"
        )

    inventory = HubInventory(
        hub_id=target_hub_id,
        part_category_id=body.part_category_id,
        quantity=body.quantity,
        min_stock_threshold=body.min_stock_threshold,
        updated_at=now,
    )
    db.add(inventory)
    await db.commit()
    await db.refresh(inventory)
    return inventory


@router.put("/hub-inventories/{inventory_id}", response_model=HubInventoryResponse, summary="Cập nhật tồn kho")
async def update_hub_inventory(
    inventory_id: int,
    body: HubInventoryUpdate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Cập nhật tồn kho vật tư (quantity, min_stock_threshold).
    - TECHNICIAN: chỉ cập nhật tồn kho tại Hub của mình.
    - ADMIN: cập nhật bất kỳ.
    """
    inventory = await db.get(HubInventory, inventory_id)
    if not inventory:
        raise HTTPException(status_code=404, detail="Hub inventory not found")

    # Permission check
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id or inventory.hub_id != int(user.hub_id):
            raise HTTPException(status_code=403, detail="Tồn kho không thuộc Hub của bạn")

    if body.quantity is not None:
        inventory.quantity = body.quantity
    if body.min_stock_threshold is not None:
        inventory.min_stock_threshold = body.min_stock_threshold

    inventory.updated_at = get_vn_naive_datetime()
    await db.commit()
    await db.refresh(inventory)
    return inventory


@router.patch("/hub-inventories/{inventory_id}/adjust", response_model=HubInventoryResponse, summary="Điều chỉnh số lượng tồn kho")
async def adjust_hub_inventory_quantity(
    inventory_id: int,
    delta: int,
    reason: Optional[str] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Điều chỉnh số lượng tồn kho theo delta (có thể âm để trừ tồn kho).
    VD: PATCH /hub-inventories/1/adjust?delta=-2&reason=used_for_repair
    
    - delta: số lượng thay đổi (âm để trừ, dương để cộng)
    - reason: lý do điều chỉnh (optional, vd: "used_for_repair", "inventory_correction")
    """
    inventory = await db.get(HubInventory, inventory_id)
    if not inventory:
        raise HTTPException(status_code=404, detail="Hub inventory not found")

    # Permission check
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id or inventory.hub_id != int(user.hub_id):
            raise HTTPException(status_code=403, detail="Tồn kho không thuộc Hub của bạn")

    new_qty = inventory.quantity + delta
    if new_qty < 0:
        raise HTTPException(
            status_code=400,
            detail=f"Số lượng điều chỉnh không hợp lệ: {inventory.quantity} + ({delta}) = {new_qty} < 0"
        )

    inventory.quantity = new_qty
    inventory.updated_at = get_vn_naive_datetime()

    await db.commit()
    await db.refresh(inventory)
    return inventory


@router.delete("/hub-inventories/{inventory_id}", status_code=204, summary="Xóa tồn kho")
async def delete_hub_inventory(
    inventory_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Xóa bản ghi tồn kho (thường khi loại vật tư không còn sử dụng tại hub này).
    - TECHNICIAN: chỉ xóa tồn kho tại Hub của mình.
    - ADMIN: xóa bất kỳ.
    """
    inventory = await db.get(HubInventory, inventory_id)
    if not inventory:
        raise HTTPException(status_code=404, detail="Hub inventory not found")

    # Permission check
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id or inventory.hub_id != int(user.hub_id):
            raise HTTPException(status_code=403, detail="Tồn kho không thuộc Hub của bạn")

    await db.delete(inventory)
    await db.commit()


# ==================== PARTS REQUEST ENDPOINTS ====================

@router.post("/parts-requests", response_model=PartsRequestResponse, status_code=201, summary="Technician: Yêu cầu cấp phát vật tư")
async def create_parts_request(
    body: PartsRequestCreate,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician gửi yêu cầu cấp phát vật tư.
    - Tự động lấy technician_id từ user.id.
    - Tự động lấy hub_id từ user.hub_id.
    """
    if not user.hub_id:
        raise HTTPException(status_code=400, detail="Technician chưa được gán Hub nào")

    # Verify part category exists
    part_category = await db.get(PartCategory, body.part_category_id)
    if not part_category:
        raise HTTPException(status_code=404, detail=f"Part category #{body.part_category_id} không tồn tại")

    now = get_vn_naive_datetime()

    parts_request = PartsRequest(
        requested_by=str(user.id),
        hub_id=int(user.hub_id),
        drone_id=body.drone_id,
        part_category_id=body.part_category_id,
        quantity_needed=body.quantity_needed,
        description=body.description,
        photo_urls=body.photo_urls,
        status=PartsRequestStatus.PENDING,
        created_at=now,
    )
    db.add(parts_request)
    await db.commit()
    await db.refresh(parts_request)
    return parts_request


@router.get("/parts-requests/my", response_model=List[PartsRequestResponse], summary="Technician: Xem yêu cầu của mình")
async def get_my_parts_requests(
    status: Optional[str] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Technician xem danh sách yêu cầu cấp phát của mình.
    - Query param `status=pending|approved|rejected|fulfilled` để lọc.
    """
    query = (
        select(PartsRequest)
        .where(PartsRequest.requested_by == str(user.id))
        .order_by(PartsRequest.created_at.desc())
    )

    if status:
        try:
            status_enum = PartsRequestStatus(status)
            query = query.where(PartsRequest.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/parts-requests", response_model=List[PartsRequestResponse], summary="Admin: Xem tất cả yêu cầu cấp phát")
async def list_all_parts_requests(
    hub_id: Optional[int] = None,
    status: Optional[str] = None,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    ADMIN ONLY: Xem tất cả yêu cầu cấp phát vật tư (có thể lọc theo hub_id hoặc status).
    """
    query = select(PartsRequest).order_by(PartsRequest.created_at.desc())

    if hub_id is not None:
        query = query.where(PartsRequest.hub_id == hub_id)

    if status:
        try:
            status_enum = PartsRequestStatus(status)
            query = query.where(PartsRequest.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/parts-requests/{request_id}", response_model=PartsRequestResponse, summary="Lấy chi tiết yêu cầu cấp phát")
async def get_parts_request(
    request_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """Lấy chi tiết yêu cầu cấp phát (technician chỉ xem của mình, admin xem tất cả)."""
    parts_request = await db.get(PartsRequest, request_id)
    if not parts_request:
        raise HTTPException(status_code=404, detail="Parts request not found")

    # Permission check
    if user.role == UserRole.TECHNICIAN:
        if str(parts_request.requested_by) != str(user.id):
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem yêu cầu này")

    return parts_request


@router.post("/parts-requests/{request_id}/approve", response_model=PartsRequestResponse, summary="Admin: Duyệt yêu cầu cấp phát")
async def approve_parts_request(
    request_id: int,
    body: PartsRequestApprove,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    ADMIN ONLY: Duyệt yêu cầu cấp phát vật tư.
    - Chuyển status từ PENDING → APPROVED.
    - Lưu reviewed_by, admin_note, reviewed_at, actual_cost.
    """
    parts_request = await db.get(PartsRequest, request_id)
    if not parts_request:
        raise HTTPException(status_code=404, detail="Parts request not found")

    if parts_request.status != PartsRequestStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ có thể duyệt yêu cầu ở trạng thái PENDING, hiện tại là {parts_request.status.value}"
        )

    now = get_vn_naive_datetime()
    parts_request.status = PartsRequestStatus.APPROVED
    parts_request.reviewed_by = str(user.id)
    parts_request.admin_note = body.admin_note
    parts_request.reviewed_at = now

    # Calculate actual cost if available
    part_category = await db.get(PartCategory, parts_request.part_category_id)
    if part_category and part_category.unit_cost:
        parts_request.actual_cost = part_category.unit_cost * parts_request.quantity_needed

    await db.commit()
    await db.refresh(parts_request)
    return parts_request


@router.post("/parts-requests/{request_id}/reject", response_model=PartsRequestResponse, summary="Admin: Từ chối yêu cầu cấp phát")
async def reject_parts_request(
    request_id: int,
    body: PartsRequestReject,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    ADMIN ONLY: Từ chối yêu cầu cấp phát vật tư.
    - Chuyển status từ PENDING → REJECTED.
    - Lưu reviewed_by, admin_note, reviewed_at.
    """
    parts_request = await db.get(PartsRequest, request_id)
    if not parts_request:
        raise HTTPException(status_code=404, detail="Parts request not found")

    if parts_request.status != PartsRequestStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ có thể từ chối yêu cầu ở trạng thái PENDING, hiện tại là {parts_request.status.value}"
        )

    now = get_vn_naive_datetime()
    parts_request.status = PartsRequestStatus.REJECTED
    parts_request.reviewed_by = str(user.id)
    parts_request.admin_note = body.admin_note
    parts_request.reviewed_at = now

    await db.commit()
    await db.refresh(parts_request)
    return parts_request


@router.post("/parts-requests/{request_id}/fulfill", response_model=PartsRequestResponse, summary="Admin/Technician: Xác nhận nhập hàng & cập nhật tồn kho")
async def fulfill_parts_request(
    request_id: int,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Xác nhận nhập hàng & tự động cập nhật tồn kho HubInventory.
    - ADMIN hoặc TECHNICIAN (tại hub của mình) có thể fulfill.
    - Chuyển status từ APPROVED → FULFILLED.
    - Tự động tăng quantity trong HubInventory tương ứng.
    """
    parts_request = await db.get(PartsRequest, request_id)
    if not parts_request:
        raise HTTPException(status_code=404, detail="Parts request not found")

    if parts_request.status != PartsRequestStatus.APPROVED:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ có thể fulfill yêu cầu ở trạng thái APPROVED, hiện tại là {parts_request.status.value}"
        )

    # Permission check
    if user.role == UserRole.TECHNICIAN:
        if not user.hub_id or parts_request.hub_id != int(user.hub_id):
            raise HTTPException(status_code=403, detail="Yêu cầu không thuộc Hub của bạn")

    now = get_vn_naive_datetime()

    # Update HubInventory
    inventory = await db.execute(
        select(HubInventory).where(
            (HubInventory.hub_id == parts_request.hub_id) &
            (HubInventory.part_category_id == parts_request.part_category_id)
        )
    )
    inv = inventory.scalar_one_or_none()

    if inv:
        # Increase quantity
        inv.quantity += parts_request.quantity_needed
        inv.updated_at = now
    else:
        # Create new inventory record if doesn't exist
        inv = HubInventory(
            hub_id=parts_request.hub_id,
            part_category_id=parts_request.part_category_id,
            quantity=parts_request.quantity_needed,
            min_stock_threshold=5,
            updated_at=now,
        )
        db.add(inv)

    # Update parts request
    parts_request.status = PartsRequestStatus.FULFILLED
    parts_request.fulfilled_at = now

    await db.commit()
    await db.refresh(parts_request)
    return parts_request
