from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, MissionStatus
from app.modules.missions.schemas import MissionKPIResponse, MissionStatusResponse, ActiveFlightsResponse, MissionResponse, ApproveRejectRequest

router = APIRouter(prefix="/missions", tags=["Missions"])

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])
allow_operator_and_customer = RoleChecker([UserRole.OPERATOR, UserRole.CUSTOMER])


# ── Operator Dashboard ────────────────────────────────────────────────────────

@router.get("/dashboard/kpi", response_model=MissionKPIResponse)
async def get_mission_kpi(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(Mission))
    missions = result.scalars().all()

    total = len(missions)
    completed = sum(1 for m in missions if m.status == MissionStatus.COMPLETED)
    cancelled = sum(1 for m in missions if m.status == MissionStatus.CANCELLED)
    rejected = sum(1 for m in missions if m.status == MissionStatus.REJECTED)
    finished = completed + cancelled + rejected

    distances = [m.distance_m for m in missions if m.distance_m]
    payloads = [m.payload_weight for m in missions if m.payload_weight]
    revenues = [m.delivery_fee for m in missions if m.delivery_fee and m.status == MissionStatus.COMPLETED]

    return MissionKPIResponse(
        total_missions=total,
        completed=completed,
        cancelled=cancelled,
        rejected=rejected,
        success_rate=round(completed / finished * 100, 2) if finished else 0.0,
        avg_distance_m=round(sum(distances) / len(distances), 2) if distances else 0.0,
        avg_payload_kg=round(sum(payloads) / len(payloads), 2) if payloads else 0.0,
        total_revenue=round(sum(revenues), 2),
    )


@router.get("/dashboard/status", response_model=MissionStatusResponse)
async def get_mission_status(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Mission.status, func.count().label("count")).group_by(Mission.status)
    )
    rows = result.all()
    return MissionStatusResponse(
        status_breakdown={row.status.value: row.count for row in rows}
    )


@router.get("/dashboard/active-flights", response_model=ActiveFlightsResponse)
async def get_active_flights(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Mission).where(Mission.status == MissionStatus.FLYING)
    )
    missions = result.scalars().all()
    return ActiveFlightsResponse(
        active_count=len(missions),
        missions=[
            {
                "id": m.id,
                "drone_id": m.drone_id,
                "operator_id": m.operator_id,
                "payload_weight": m.payload_weight,
                "distance_m": m.distance_m,
                "start_time": m.start_time.isoformat() if m.start_time else None,
            }
            for m in missions
        ],
    )


# ── General ───────────────────────────────────────────────────────────────────

@router.post("/request")
async def create_mission_request(user: CurrentUser = Depends(allow_operator_and_customer)):
    return {"message": "Mission request created."}


@router.get("/", response_model=list[MissionResponse])
async def get_missions(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(Mission).order_by(Mission.id.desc()))
    return result.scalars().all()


# ── Operator: Planned Missions & Approval ─────────────────────────────────────

@router.get("/planned", response_model=list[MissionResponse])
async def get_planned_missions(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Mission)
        .where(Mission.status == MissionStatus.PENDING_APPROVAL)
        .order_by(Mission.scheduled_time)
    )
    return result.scalars().all()


@router.post("/{mission_id}/approve", response_model=MissionResponse)
async def approve_mission(
    mission_id: int,
    body: ApproveRejectRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status != MissionStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=400, detail=f"Cannot approve mission with status '{mission.status.value}'")
    mission.status = MissionStatus.APPROVED
    if body.drone_id:
        mission.drone_id = body.drone_id
    if body.battery_id:
        mission.battery_id = body.battery_id
    if body.operator_id:
        mission.operator_id = body.operator_id
    await db.commit()
    await db.refresh(mission)
    return mission


@router.post("/{mission_id}/reject", response_model=MissionResponse)
async def reject_mission(
    mission_id: int,
    body: ApproveRejectRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status != MissionStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=400, detail=f"Cannot reject mission with status '{mission.status.value}'")
    mission.status = MissionStatus.REJECTED
    await db.commit()
    await db.refresh(mission)
    return mission
