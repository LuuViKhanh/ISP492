"""
Tools dành cho Admin Chatbot.
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, and_

try:
    from google.genai import types
except (ImportError, ModuleNotFoundError):
    types = None

from app.modules.auth.models import User
from app.modules.missions.models import Mission, MissionStatus, Order
from app.modules.fleet.models import Drone


async def tool_get_system_executive_summary(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Báo cáo tổng quan hoạt động kinh doanh và tài nguyên toàn hệ thống."""
    comp_cond = Mission.status.in_((MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED))
    row = (await db.execute(
        select(
            select(func.count(Mission.id)).scalar_subquery(),
            select(func.count(Mission.id)).where(comp_cond).scalar_subquery(),
            select(func.coalesce(func.sum(Mission.delivery_fee), 0.0)).where(comp_cond).scalar_subquery(),
            select(func.count(Order.id)).scalar_subquery(),
            select(func.count(User.id)).scalar_subquery(),
            select(func.count(Drone.id)).scalar_subquery(),
        )
    )).one()

    total_missions = int(row[0] or 0)
    completed_missions = int(row[1] or 0)
    revenue = float(row[2] or 0.0)
    total_orders = int(row[3] or 0)
    total_users = int(row[4] or 0)
    total_drones = int(row[5] or 0)

    return {
        "success": True,
        "total_revenue_vnd": f"{int(revenue):,} VND",
        "raw_revenue": revenue,
        "total_orders": total_orders,
        "total_missions": total_missions,
        "completed_missions": completed_missions,
        "total_registered_users": total_users,
        "total_fleet_drones": total_drones
    }


async def tool_get_user_role_counts(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Thống kê cơ cấu người dùng theo vai trò."""
    stmt = select(User.role, func.count(User.id)).group_by(User.role)
    result = await db.execute(stmt)
    rows = result.all()

    breakdown = {str(role.value if hasattr(role, 'value') else role): count for role, count in rows}

    return {
        "success": True,
        "total_users": sum(breakdown.values()),
        "role_breakdown": breakdown
    }


from app.modules.chatbot.rag.tool import get_rag_function_declaration, make_rag_tool_for_role

_tool_search_kb_admin = make_rag_tool_for_role("admin")


def get_admin_gemini_tools() -> list:
    if types is None:
        return []
    rag_decl = get_rag_function_declaration()
    decls = [
                types.FunctionDeclaration(
                    name="get_system_executive_summary",
                    description="Get the executive summary report (total revenue, orders, completed flight missions, and fleet drone count).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_user_role_counts",
                    description="Get user account statistics grouped by role (Admin, Operator, Technician, Customer).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
    ]
    if rag_decl:
        decls.append(rag_decl)
    return [types.Tool(function_declarations=decls)]


ADMIN_TOOLS_MAP = {
    "get_system_executive_summary": tool_get_system_executive_summary,
    "get_user_role_counts": tool_get_user_role_counts,
    "search_knowledge_base": _tool_search_kb_admin,
}
