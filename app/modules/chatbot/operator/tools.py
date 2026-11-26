"""
Tools dành cho Operator Chatbot.
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

try:
    from google.genai import types
except (ImportError, ModuleNotFoundError):
    types = None

from app.modules.missions.models import Mission, MissionStatus, Incident


async def tool_get_active_missions(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Lấy danh sách các chuyến bay đang hoạt động trên bầu trời."""
    stmt = select(Mission).where(
        Mission.status == MissionStatus.FLYING
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
    from sqlalchemy import case, and_
    comp_cond = Mission.status.in_((MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED))
    row = (await db.execute(
        select(
            func.count(Mission.id),
            func.count(case((comp_cond, 1))),
            func.count(case((Mission.status == MissionStatus.CANCELLED, 1))),
            func.coalesce(func.sum(case((and_(comp_cond, Mission.delivery_fee.isnot(None)), Mission.delivery_fee), else_=0.0)), 0.0),
        )
    )).one()

    total = int(row[0] or 0)
    completed = int(row[1] or 0)
    cancelled = int(row[2] or 0)
    rejected = 0
    finished = completed + cancelled + rejected
    revenue = float(row[3] or 0.0)

    return {
        "success": True,
        "total_missions": total,
        "completed": completed,
        "cancelled": cancelled,
        "rejected": rejected,
        "success_rate_pct": round(completed / finished * 100, 2) if finished else 0.0,
        "total_revenue_vnd": f"{int(revenue):,} VND"
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
        return {"success": False, "message": f"Mission with ID '{mission_id}' was not found."}

    return {
        "success": True,
        "mission_id": m.id,
        "drone_id": m.drone_id,
        "status": m.status.value if m.status else "UNKNOWN",
        "payload_weight": m.payload_weight,
        "distance_m": m.distance_m,
        "delivery_fee": f"{int(m.delivery_fee):,} VND" if m.delivery_fee else None,
        "scheduled_time": m.scheduled_time.strftime("%H:%M %d/%m/%Y") if m.scheduled_time else None,
        "start_time": m.start_time.strftime("%H:%M %d/%m/%Y") if m.start_time else None,
        "end_time": m.end_time.strftime("%H:%M %d/%m/%Y") if m.end_time else None,
    }


from app.modules.chatbot.rag.tool import get_rag_function_declaration, make_rag_tool_for_role

_tool_search_kb_operator = make_rag_tool_for_role("operator")


def get_operator_gemini_tools() -> list:
    if types is None:
        return []
    rag_decl = get_rag_function_declaration()
    decls = [
                types.FunctionDeclaration(
                    name="get_active_missions",
                    description="Get the list of drone flight missions currently active in the air.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_operator_kpi",
                    description="Get operational statistics (total missions, completed, cancelled, rejected, success rate, and revenue).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_recent_incidents",
                    description="View the list of recent flight incidents across the drone fleet.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "limit": types.Schema(type=types.Type.INTEGER, description="Number of recent incidents to retrieve.")
                        }
                    )
                ),
                types.FunctionDeclaration(
                    name="search_mission_by_id",
                    description="Look up detailed information for a flight mission by Mission ID.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "mission_id": types.Schema(type=types.Type.STRING, description="Flight mission ID.")
                        },
                        required=["mission_id"]
                    )
                ),
    ]
    if rag_decl:
        decls.append(rag_decl)
    return [types.Tool(function_declarations=decls)]


OPERATOR_TOOLS_MAP = {
    "get_active_missions": tool_get_active_missions,
    "get_operator_kpi": tool_get_operator_kpi,
    "get_recent_incidents": tool_get_recent_incidents,
    "search_mission_by_id": tool_search_mission_by_id,
    "search_knowledge_base": _tool_search_kb_operator,
}
