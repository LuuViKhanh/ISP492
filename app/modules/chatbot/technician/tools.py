"""
Tools dành cho Technician Chatbot.
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

try:
    from google.genai import types
except (ImportError, ModuleNotFoundError):
    types = None

from app.modules.fleet.models import Drone, DroneStatus, Battery, WorkOrder, WorkOrderStatus


async def tool_get_fleet_status(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Lấy tổng quan tình trạng đội Drone."""
    result = await db.execute(select(Drone))
    drones = result.scalars().all()

    total = len(drones)
    available = sum(1 for d in drones if d.status == DroneStatus.AVAILABLE)
    in_flight = sum(1 for d in drones if d.status == DroneStatus.IN_FLIGHT)
    maintenance = sum(1 for d in drones if d.status == DroneStatus.MAINTENANCE)

    drone_summary = [
        {
            "id": d.id,
            "name": d.name,
            "model": d.model,
            "status": d.status.value if d.status else "UNKNOWN",
            "battery_pct": d.battery_level_pct
        }
        for d in drones[:10]
    ]

    return {
        "success": True,
        "total_drones": total,
        "available_count": available,
        "in_flight_count": in_flight,
        "maintenance_count": maintenance,
        "sample_drones": drone_summary
    }


async def tool_get_battery_status(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Kiểm tra tình trạng pin (đặc biệt là pin yếu cần sạc)."""
    result = await db.execute(select(Battery))
    batteries = result.scalars().all()

    low_batteries = [
        {
            "id": b.id,
            "serial": b.serial_number,
            "charge_pct": b.charge_level_pct,
            "hub_id": b.current_hub_id,
            "drone_id": b.drone_id
        }
        for b in batteries
        if b.charge_level_pct is not None and b.charge_level_pct < 30
    ]

    return {
        "success": True,
        "total_batteries": len(batteries),
        "low_battery_count": len(low_batteries),
        "low_batteries_needing_charge": low_batteries
    }


async def tool_get_pending_work_orders(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Lấy danh sách các lệnh sửa chữa/bảo dưỡng đang chờ hoặc đang thực hiện."""
    stmt = select(WorkOrder).where(
        WorkOrder.status.in_([WorkOrderStatus.PENDING, WorkOrderStatus.IN_PROGRESS])
    )
    result = await db.execute(stmt)
    work_orders = result.scalars().all()

    order_list = [
        {
            "id": wo.id,
            "drone_id": wo.drone_id,
            "technician_id": wo.technician_id,
            "status": wo.status.value if wo.status else "UNKNOWN",
            "issue": wo.issue_description
        }
        for wo in work_orders
    ]

    return {
        "success": True,
        "pending_work_order_count": len(order_list),
        "work_orders": order_list
    }


def get_technician_gemini_tools() -> list:
    if types is None:
        return []
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="get_fleet_status",
                    description="Xem tổng quan tình trạng đội Drone (sẵn sàng, đang bay, đang bảo trì).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_battery_status",
                    description="Kiểm tra pin hệ thống và danh sách các pin yếu cần sạc gấp (<30%).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_pending_work_orders",
                    description="Xem danh sách các lệnh sửa chữa và bảo dưỡng (Work Orders) chưa hoàn thành.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
            ]
        )
    ]


TECHNICIAN_TOOLS_MAP = {
    "get_fleet_status": tool_get_fleet_status,
    "get_battery_status": tool_get_battery_status,
    "get_pending_work_orders": tool_get_pending_work_orders,
}
