"""
Tools dành cho Operator Chatbot.
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from google.genai import types

from app.modules.missions.models import Mission, MissionStatus, Incident


async def tool_get_active_missions(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Lấy danh sách các chuyến bay đang hoạt động trên bầu trời."""
    stmt = select(Mission).where(
        Mission.status.in_([MissionStatus.FLYING, MissionStatus.IN_PROGRESS])
    )
    result = await db.execute(stmt)
    missions = result.scalars().all()

    flight_list = [
        {
            "mission_id": m.id,
            "drone_id": m.drone_id,
            "operator_id": m.operator_id,
            "payload_kg": m.payload_weight,
            "distance_m": m.distance_m,
            "start_time": m.start_time.strftime("%H:%M %d/%m/%Y") if m.start_time else None
        }
        for m in missions
    ]

    return {
        "success": True,
        "active_flight_count": len(flight_list),
        "active_flights": flight_list
    }


async def tool_get_operator_kpi(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Lấy thống kê hiệu suất KPI điều hành bay."""
    result = await db.execute(select(Mission))
    missions = result.scalars().all()

    total = len(missions)
    completed = sum(1 for m in missions if m.status == MissionStatus.COMPLETED)
    cancelled = sum(1 for m in missions if m.status == MissionStatus.CANCELLED)
    rejected = sum(1 for m in missions if m.status == MissionStatus.REJECTED)
    finished = completed + cancelled + rejected
    revenue = sum(m.delivery_fee for m in missions if m.delivery_fee and m.status == MissionStatus.COMPLETED)

    return {
        "success": True,
        "total_missions": total,
        "completed": completed,
        "cancelled": cancelled,
        "rejected": rejected,
        "success_rate_pct": round(completed / finished * 100, 2) if finished else 0.0,
        "total_revenue_vnd": f"{int(revenue):,} VNĐ"
    }


async def tool_get_recent_incidents(db: AsyncSession, limit: int = 5, **kwargs) -> Dict[str, Any]:
    """Lấy danh sách sự cố bay gần đây."""
    stmt = select(Incident).order_by(Incident.reported_at.desc()).limit(limit)
    result = await db.execute(stmt)
    incidents = result.scalars().all()

    incident_list = [
        {
            "id": inc.id,
            "mission_id": inc.mission_id,
            "drone_id": inc.drone_id,
            "severity": inc.severity.value if inc.severity else "UNKNOWN",
            "status": inc.status.value if inc.status else "UNKNOWN",
            "description": inc.description,
            "reported_at": inc.reported_at.strftime("%H:%M %d/%m/%Y") if inc.reported_at else None
        }
        for inc in incidents
    ]

    return {
        "success": True,
        "total_recent_incidents": len(incident_list),
        "incidents": incident_list
    }


async def tool_search_mission_by_id(db: AsyncSession, mission_id: str, **kwargs) -> Dict[str, Any]:
    """Tra cứu chi tiết một chuyến bay theo Mission ID."""
    clean_id = mission_id.strip()
    stmt = select(Mission).where(Mission.id.ilike(clean_id))
    result = await db.execute(stmt)
    m = result.scalar_one_or_none()

    if not m:
        return {"success": False, "message": f"Không tìm thấy chuyến bay có ID '{mission_id}'."}

    return {
        "success": True,
        "mission_id": m.id,
        "drone_id": m.drone_id,
        "status": m.status.value if m.status else "UNKNOWN",
        "payload_weight": m.payload_weight,
        "distance_m": m.distance_m,
        "delivery_fee": f"{int(m.delivery_fee):,} VNĐ" if m.delivery_fee else None,
        "scheduled_time": m.scheduled_time.strftime("%H:%M %d/%m/%Y") if m.scheduled_time else None,
        "start_time": m.start_time.strftime("%H:%M %d/%m/%Y") if m.start_time else None,
        "end_time": m.end_time.strftime("%H:%M %d/%m/%Y") if m.end_time else None,
    }


def get_operator_gemini_tools() -> List[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="get_active_missions",
                    description="Lấy danh sách các chuyến bay Drone đang hoạt động trên bầu trời.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_operator_kpi",
                    description="Lấy số liệu thống kê vận hành (tổng số chuyến, hoàn thành, tỷ lệ thành công, doanh thu).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_recent_incidents",
                    description="Xem danh sách các sự cố bay gần đây của phi đội Drone.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "limit": types.Schema(type=types.Type.INTEGER, description="Số lượng sự cố cần lấy.")
                        }
                    )
                ),
                types.FunctionDeclaration(
                    name="search_mission_by_id",
                    description="Tra cứu chi tiết chuyến bay theo Mission ID.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "mission_id": types.Schema(type=types.Type.STRING, description="Mã chuyến bay.")
                        },
                        required=["mission_id"]
                    )
                ),
            ]
        )
    ]


OPERATOR_TOOLS_MAP = {
    "get_active_missions": tool_get_active_missions,
    "get_operator_kpi": tool_get_operator_kpi,
    "get_recent_incidents": tool_get_recent_incidents,
    "search_mission_by_id": tool_search_mission_by_id,
}
