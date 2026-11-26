"""
Tools dành cho Technician Chatbot.
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

try:
    from google.genai import types
except (ImportError, ModuleNotFoundError):
    types = None

from app.modules.fleet.models import Drone, DroneStatus, Battery, WorkOrder, WorkOrderStatus


async def tool_get_fleet_status(db: AsyncSession, hub_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Lấy tổng quan tình trạng đội Drone tại hub của technician."""
    drone_scope = [] if hub_id is None else [Drone.current_hub_id == hub_id]
    counts_rows = (await db.execute(
        select(Drone.status, func.count(Drone.id)).where(*drone_scope).group_by(Drone.status)
    )).all()
    counts = {st: int(cnt) for st, cnt in counts_rows}
    total = sum(counts.values())
    available = counts.get(DroneStatus.AVAILABLE, 0)
    in_flight = counts.get(DroneStatus.IN_FLIGHT, 0)
    maintenance = counts.get(DroneStatus.MAINTENANCE, 0)

    sample_rows = (await db.execute(
        select(Drone.id, Drone.name, Drone.model, Drone.status, Drone.battery_level_pct)
        .where(*drone_scope).limit(10)
    )).all()
    drone_summary = [
        {
            "id": r.id,
            "name": r.name,
            "model": r.model,
            "status": r.status.value if r.status else "UNKNOWN",
            "battery_pct": r.battery_level_pct
        }
        for r in sample_rows
    ]

    return {
        "success": True,
        "total_drones": total,
        "available_count": available,
        "in_flight_count": in_flight,
        "maintenance_count": maintenance,
        "sample_drones": drone_summary
    }


async def tool_get_battery_status(db: AsyncSession, hub_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Kiểm tra tình trạng pin tại hub của technician (pin đặt ở hub hoặc đang gắn trên drone của hub)."""
    if hub_id is None:
        battery_scope = []
    else:
        hub_drone_ids = select(Drone.id).where(Drone.current_hub_id == hub_id)
        battery_scope = [or_(Battery.current_hub_id == hub_id, Battery.drone_id.in_(hub_drone_ids))]
    total_batteries = int((await db.execute(
        select(func.count(Battery.id)).where(*battery_scope)
    )).scalar() or 0)
    low_rows = (await db.execute(
        select(Battery.id, Battery.serial_number, Battery.charge_level_pct, Battery.current_hub_id, Battery.drone_id)
        .where(Battery.charge_level_pct.isnot(None), Battery.charge_level_pct < 30, *battery_scope)
    )).all()

    low_batteries = [
        {
            "id": b.id,
            "serial": b.serial_number,
            "charge_pct": b.charge_level_pct,
            "hub_id": b.current_hub_id,
            "drone_id": b.drone_id
        }
        for b in low_rows
    ]

    return {
        "success": True,
        "total_batteries": total_batteries,
        "low_battery_count": len(low_batteries),
        "low_batteries_needing_charge": low_batteries
    }


async def tool_get_pending_work_orders(db: AsyncSession, hub_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Lấy danh sách lệnh sửa chữa/bảo dưỡng đang chờ hoặc đang thực hiện của drone tại hub."""
    stmt = select(WorkOrder).where(
        WorkOrder.status.in_([WorkOrderStatus.PENDING, WorkOrderStatus.IN_PROGRESS])
    )
    if hub_id is not None:
        stmt = stmt.where(WorkOrder.drone_id.in_(select(Drone.id).where(Drone.current_hub_id == hub_id)))
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


from app.modules.chatbot.rag.tool import get_rag_function_declaration, make_rag_tool_for_role

_tool_search_kb_technician = make_rag_tool_for_role("technician")


def get_technician_gemini_tools() -> list:
    if types is None:
        return []
    rag_decl = get_rag_function_declaration()
    decls = [
                types.FunctionDeclaration(
                    name="get_fleet_status",
                    description="View an overview of the drone fleet status (available, in flight, and under maintenance).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_battery_status",
                    description="Check system batteries and list low-charge batteries requiring urgent charging (<30%).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_pending_work_orders",
                    description="View the list of unfinished repair and maintenance work orders.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
    ]
    if rag_decl:
        decls.append(rag_decl)
    return [types.Tool(function_declarations=decls)]


def build_technician_tools_map(hub_id: Optional[int]) -> Dict[str, Any]:
    """
    Tạo tools_map đã gắn phạm vi dữ liệu theo hub của người dùng.
    - hub_id là số: chỉ thấy dữ liệu của hub đó.
    - hub_id là None: không có phạm vi lọc (chỉ dùng cho Admin).
    Technician không có hub thì router phải truyền -1 để không thấy dữ liệu nào.
    """
    def bind(fn):
        async def _wrapped(db, **kwargs):
            return await fn(db, hub_id=hub_id, **kwargs)
        return _wrapped

    return {
        "get_fleet_status": bind(tool_get_fleet_status),
        "get_battery_status": bind(tool_get_battery_status),
        "get_pending_work_orders": bind(tool_get_pending_work_orders),
        "search_knowledge_base": _tool_search_kb_technician,
    }


TECHNICIAN_TOOLS_MAP = {
    "get_fleet_status": tool_get_fleet_status,
    "get_battery_status": tool_get_battery_status,
    "get_pending_work_orders": tool_get_pending_work_orders,
    "search_knowledge_base": _tool_search_kb_technician,
}
