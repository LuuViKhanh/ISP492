"""
Tools dành cho Admin Chatbot.
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from google.genai import types

from app.modules.auth.models import User
from app.modules.missions.models import Mission, MissionStatus, Order
from app.modules.fleet.models import Drone


async def tool_get_system_executive_summary(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Báo cáo tổng quan hoạt động kinh doanh và tài nguyên toàn hệ thống."""
    # Missions
    missions_res = await db.execute(select(Mission))
    missions = missions_res.scalars().all()
    total_missions = len(missions)
    completed_missions = sum(1 for m in missions if m.status == MissionStatus.COMPLETED)
    revenue = sum(m.delivery_fee for m in missions if m.delivery_fee and m.status == MissionStatus.COMPLETED)

    # Orders
    orders_res = await db.execute(select(func.count(Order.id)))
    total_orders = orders_res.scalar() or 0

    # Users
    users_res = await db.execute(select(func.count(User.id)))
    total_users = users_res.scalar() or 0

    # Drones
    drones_res = await db.execute(select(func.count(Drone.id)))
    total_drones = drones_res.scalar() or 0

    return {
        "success": True,
        "total_revenue_vnd": f"{int(revenue):,} VNĐ",
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


def get_admin_gemini_tools() -> List[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="get_system_executive_summary",
                    description="Lấy báo cáo tổng quan điều hành (doanh thu, đơn hàng, chuyến bay hoàn thành, số lượng drone).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_user_role_counts",
                    description="Thống kê số lượng người dùng theo từng vai trò (Admin, Operator, Technician, Customer).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
            ]
        )
    ]


ADMIN_TOOLS_MAP = {
    "get_system_executive_summary": tool_get_system_executive_summary,
    "get_user_role_counts": tool_get_user_role_counts,
}
