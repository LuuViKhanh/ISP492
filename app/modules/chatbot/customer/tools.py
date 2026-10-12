"""
Tools nghiệp vụ dành riêng cho Khách hàng (Customer Chatbot Tools).
-------------------------------------------------------------------
GHI CHÚ CHO LẬP TRÌNH VIÊN (DEVELOPER NOTES):
- Chứa các hàm nghiệp vụ phục vụ khách hàng: Tra cứu đơn, tính phí ship, xem Hub, xem đơn gần đây.
- Tái sử dụng logic tính phí `calculate_delivery_cost` và AI `predict_flight_energy`.
- Tương tác đọc dữ liệu Database (không làm thay đổi dữ liệu DB).
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

try:
    from google.genai import types
except (ImportError, ModuleNotFoundError):
    types = None

from app.modules.missions.models import Order, Mission, OrderStatus, DeliveryMode
from app.modules.system.models import Hub
from app.modules.orders.customer.router import calculate_delivery_cost
from app.modules.ai_predictions.service import haversine_distance, predict_flight_energy


# ==============================================================================
# HÀM THỰC THI NGHIỆP VỤ PYTHON
# ==============================================================================
async def tool_lookup_order(
    db: AsyncSession,
    order_id: str,
    current_user_id: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """Tra cứu chi tiết một đơn hàng theo mã đơn."""
    clean_id = order_id.strip()
    stmt = select(Order).where(Order.id.ilike(clean_id))
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()

    if not order:
        return {
            "success": False,
            "message": f"Không tìm thấy đơn hàng với mã '{order_id}'. Vui lòng kiểm tra lại mã đơn (thường có dạng ORD-xxxx)."
        }

    origin_name = "Chưa xác định"
    dest_name = "Chưa xác định"
    if order.origin_hub_id:
        orig = await db.get(Hub, order.origin_hub_id)
        if orig:
            origin_name = orig.name
    if order.destination_hub_id:
        dest = await db.get(Hub, order.destination_hub_id)
        if dest:
            dest_name = dest.name

    mission_stmt = select(Mission).where(Mission.order_id == order.id)
    mission_res = await db.execute(mission_stmt)
    mission = mission_res.scalars().first()

    status_map = {
        OrderStatus.PENDING: "Đang chờ xử lý & xếp lịch bay",
        OrderStatus.IN_DELIVERY: "Đang được Drone vận chuyển trên không",
        OrderStatus.DELIVERED_TO_HUB: "Đã giao thành công tới trạm Hub đích",
        OrderStatus.CANCELLED: "Đã bị hủy",
    }
    status_vn = status_map.get(order.status, str(order.status.value) if order.status else "Không rõ")
    fee_str = f"{int(order.delivery_fee):,} VNĐ" if order.delivery_fee else "Chưa tính cước"

    flight_info = None
    if mission:
        flight_info = {
            "mission_id": mission.id,
            "drone_id": mission.drone_id,
            "flight_status": mission.status.value if mission.status else "Đang chuẩn bị",
            "distance_m": mission.distance_m,
            "start_time": mission.start_time.strftime("%H:%M %d/%m/%Y") if mission.start_time else None,
            "end_time": mission.end_time.strftime("%H:%M %d/%m/%Y") if mission.end_time else None,
        }

    return {
        "success": True,
        "order_id": order.id,
        "status": status_vn,
        "raw_status": order.status.value if order.status else None,
        "package_label": order.package_label or "Bưu kiện",
        "package_type": order.package_type or "standard",
        "payload_kg": order.payload_kg,
        "origin_hub": origin_name,
        "destination_hub": dest_name,
        "delivery_mode": order.delivery_mode.value if order.delivery_mode else "SCHEDULED",
        "delivery_fee": fee_str,
        "raw_fee": order.delivery_fee,
        "payment_status": order.payment_status.value if order.payment_status else "UNPAID",
        "created_at": order.created_at.strftime("%H:%M %d/%m/%Y") if order.created_at else None,
        "estimated_arrival": (
            order.estimated_window_end.strftime("%H:%M %d/%m/%Y")
            if order.estimated_window_end
            else "Đang cập nhật"
        ),
        "flight_info": flight_info
    }


async def tool_estimate_shipping_fee(
    db: AsyncSession,
    origin_hub_name: str,
    destination_hub_name: str,
    payload_kg: float = 1.0,
    delivery_mode: str = "EXPRESS",
    package_type: str = "standard",
    has_insurance: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """Tính cước vận chuyển giữa 2 Hub + dự đoán AI tiêu hao năng lượng Drone."""
    result = await db.execute(select(Hub))
    all_hubs = result.scalars().all()

    if not all_hubs:
        return {"success": False, "message": "Hệ thống hiện chưa cấu hình trạm Hub nào."}

    def find_hub(keyword: str) -> Optional[Hub]:
        kw = keyword.lower().strip()
        matches = [h for h in all_hubs if kw in h.name.lower()]
        return matches[0] if matches else None

    orig_hub = find_hub(origin_hub_name)
    dest_hub = find_hub(destination_hub_name)
    hub_names = [h.name for h in all_hubs]

    if not orig_hub or not dest_hub:
        return {
            "success": False,
            "message": (
                f"Không nhận diện được trạm Hub '{origin_hub_name}' hoặc '{destination_hub_name}'. "
                f"Danh sách các trạm Hub đang hoạt động: {', '.join(hub_names)}."
            ),
            "available_hubs": hub_names
        }

    if orig_hub.id == dest_hub.id:
        return {"success": False, "message": "Trạm xuất phát và trạm đích không được trùng nhau."}

    lat1, lon1 = orig_hub.latitude or 0.0, orig_hub.longitude or 0.0
    lat2, lon2 = dest_hub.latitude or 0.0, dest_hub.longitude or 0.0
    dist_km = haversine_distance(lat1, lon1, lat2, lon2)

    try:
        energy_prediction = predict_flight_energy(dist_km, payload_kg)
        energy_wh = round(energy_prediction.get("energy_wh", 0.0), 2)
    except Exception:
        energy_wh = round(dist_km * 25.5, 2)

    mode_enum = DeliveryMode.EXPRESS if delivery_mode.upper() == "EXPRESS" else DeliveryMode.SCHEDULED
    core_fee, service_fee, total_fee, fee_details = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=payload_kg,
        package_type=package_type,
        delivery_mode=mode_enum,
        has_insurance=has_insurance
    )

    flight_time_minutes = max(5, int(round((dist_km / 45.0) * 60 + 5)))

    return {
        "success": True,
        "origin_hub": orig_hub.name,
        "destination_hub": dest_hub.name,
        "distance_km": round(dist_km, 2),
        "payload_kg": payload_kg,
        "delivery_mode": "Hỏa tốc (Express)" if mode_enum == DeliveryMode.EXPRESS else "Tiêu chuẩn (Scheduled)",
        "package_type": "Hàng dễ vỡ" if package_type.lower() == "fragile" else "Hàng tiêu chuẩn",
        "has_insurance": has_insurance,
        "predicted_energy_wh": energy_wh,
        "estimated_flight_minutes": flight_time_minutes,
        "total_fee": f"{int(total_fee):,} VNĐ",
        "raw_total_fee": total_fee,
        "fee_breakdown": {
            "base_fee": f"{int(fee_details['base_fee']):,} VNĐ",
            "distance_fee": f"{int(fee_details['distance_fee']):,} VNĐ",
            "payload_fee": f"{int(fee_details['payload_fee']):,} VNĐ",
            "express_surcharge": f"{int(fee_details['express_surcharge']):,} VNĐ",
            "fragile_surcharge": f"{int(fee_details['fragile_surcharge']):,} VNĐ",
            "insurance_fee": f"{int(fee_details['insurance_fee']):,} VNĐ",
        }
    }


async def tool_list_hubs(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """Danh sách các trạm Hub đang hoạt động."""
    stmt = select(Hub).where(Hub.status == 'Active')
    result = await db.execute(stmt)
    hubs = result.scalars().all()
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()

    hub_list = [
        {"id": h.id, "name": h.name, "latitude": h.latitude, "longitude": h.longitude, "status": h.status}
        for h in hubs
    ]
    return {"success": True, "total_hubs": len(hub_list), "hubs": hub_list}


async def tool_get_my_recent_orders(
    db: AsyncSession,
    current_user_id: Optional[str] = None,
    limit: int = 5,
    **kwargs
) -> Dict[str, Any]:
    """Lấy đơn hàng gần đây của khách đã đăng nhập."""
    if not current_user_id:
        return {
            "success": False,
            "message": "Bạn chưa đăng nhập. Vui lòng cung cấp mã đơn cụ thể (VD: ORD-0001) để tra cứu."
        }

    stmt = select(Order).where(Order.customer_id == current_user_id).order_by(Order.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    orders = result.scalars().all()

    if not orders:
        return {"success": True, "message": "Bạn chưa có đơn hàng nào trong hệ thống.", "orders": []}

    status_map = {
        OrderStatus.PENDING: "Đang chờ xếp lịch",
        OrderStatus.IN_DELIVERY: "Đang bay giao hàng",
        OrderStatus.DELIVERED_TO_HUB: "Đã giao đến Hub",
        OrderStatus.CANCELLED: "Đã hủy",
    }
    order_list = [
        {
            "order_id": o.id,
            "package_label": o.package_label or "Kiện hàng",
            "status": status_map.get(o.status, str(o.status.value) if o.status else "Không rõ"),
            "fee": f"{int(o.delivery_fee):,} VNĐ" if o.delivery_fee else "Chưa tính",
            "created_at": o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else None
        }
        for o in orders
    ]
    return {"success": True, "total": len(order_list), "orders": order_list}


# ==============================================================================
# SCHEMA KHAI BÁO VỚI GEMINI
# ==============================================================================
def get_customer_gemini_tools() -> list:
    if types is None:
        return []
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="lookup_order",
                    description="Tra cứu thông tin và trạng thái đơn hàng theo mã đơn (ví dụ: 'ORD-0001').",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng cần kiểm tra.")
                        },
                        required=["order_id"]
                    )
                ),
                types.FunctionDeclaration(
                    name="estimate_shipping_fee",
                    description="Tính toán cước phí và năng lượng tiêu thụ giữa 2 trạm Hub.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "origin_hub_name": types.Schema(type=types.Type.STRING, description="Tên trạm gửi."),
                            "destination_hub_name": types.Schema(type=types.Type.STRING, description="Tên trạm nhận."),
                            "payload_kg": types.Schema(type=types.Type.NUMBER, description="Khối lượng kiện hàng (kg)."),
                            "delivery_mode": types.Schema(type=types.Type.STRING, description="'EXPRESS' hoặc 'SCHEDULED'."),
                            "package_type": types.Schema(type=types.Type.STRING, description="'standard' hoặc 'fragile'."),
                            "has_insurance": types.Schema(type=types.Type.BOOLEAN, description="Có mua bảo hiểm không.")
                        },
                        required=["origin_hub_name", "destination_hub_name"]
                    )
                ),
                types.FunctionDeclaration(
                    name="list_hubs",
                    description="Lấy danh sách các trạm Hub giao nhận của DroneOptAI.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={})
                ),
                types.FunctionDeclaration(
                    name="get_my_recent_orders",
                    description="Lấy danh sách các đơn hàng gần đây của khách hàng hiện tại.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "limit": types.Schema(type=types.Type.INTEGER, description="Số đơn tối đa (mặc định 5).")
                        }
                    )
                ),
            ]
        )
    ]


CUSTOMER_TOOLS_MAP = {
    "lookup_order": tool_lookup_order,
    "estimate_shipping_fee": tool_estimate_shipping_fee,
    "list_hubs": tool_list_hubs,
    "get_my_recent_orders": tool_get_my_recent_orders,
}
