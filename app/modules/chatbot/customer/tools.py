"""
Tools nghiệp vụ dành riêng cho Khách hàng (Customer Chatbot Tools - 18 Canonical Tools).
----------------------------------------------------------------------------------------
GHI CHÚ CHO LẬP TRÌNH VIÊN (DEVELOPER NOTES):
- Triển khai đầy đủ 18 tools theo đặc tả `DroneOptAI_Customer_Chatbot_FE_BE_Sync_Spec`:
  * 8 Public/Reference Tools:
    1. `get_package_rules`
    2. `recommend_package_size`
    3. `get_pricing_rules`
    4. `estimate_shipping_fee`
    5. `get_delivery_modes`
    6. `list_hubs`
    7. `resolve_hubs_by_area`
    8. `get_help_policy`
  * 10 Personal/Authenticated Tools (Bắt buộc JWT + Kiểm tra quyền sở hữu `order.customer_id == current_user_id`):
    9. `get_my_orders`
    10. `get_latest_order`
    11. `get_order_detail`
    12. `get_customer_delivery_status`
    13. `get_payment_status`
    14. `get_handover_status`
    15. `get_delivery_eta`
    16. `check_cancellation_eligibility`
    17. `get_my_notifications`
    18. `get_profile`
- Tương tác đọc dữ liệu Database (Read-Only, không thay đổi schema hay dữ liệu DB).
- Mọi kết quả trả về đều chứa `type` chuẩn (1 trong 14 `data.type`) và `_actions` chuẩn để FE render UI Card & Action Buttons.
"""

import unicodedata
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

try:
    from google.genai import types
except (ImportError, ModuleNotFoundError):
    types = None

from app.modules.missions.models import (
    Order,
    Mission,
    OrderStatus,
    MissionStatus,
    PaymentStatus,
    DeliveryMode,
)
from app.modules.system.models import Hub, Notification
from app.modules.auth.service import get_user_by_id
from app.modules.orders.customer.router import calculate_delivery_cost
from app.modules.ai_predictions.service import haversine_distance, predict_flight_energy
from app.modules.chatbot.customer.reference_service import (
    CustomerReferenceService,
    CustomerHelpService,
    CustomerStatusService,
)


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).lower()


async def _find_customer_order(
    db: AsyncSession,
    current_user_id: Optional[str],
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Hàm bảo mật cốt lõi:
    - Bắt buộc `current_user_id` từ JWT Token.
    - Nếu có `order_id`: tìm đơn và kiểm tra `order.customer_id == current_user_id`.
    - Nếu không truyền `order_id`: tự động lấy đơn hàng mới nhất của `current_user_id`.
    """
    if not current_user_id:
        return {
            "ok": False,
            "error_payload": {
                "type": "order_status",
                "success": False,
                "code": "ORDER_ACCESS_DENIED",
                "message": (
                    "Vui lòng đăng nhập tài khoản khách hàng (gửi kèm Bearer Token) "
                    "để tra cứu thông tin đơn hàng cá nhân."
                ),
            },
        }

    uid_str = str(current_user_id)

    if order_id:
        clean_id = order_id.strip()
        stmt = select(Order).where(Order.id.ilike(clean_id))
        res = await db.execute(stmt)
        order = res.scalar_one_or_none()

        if not order:
            return {
                "ok": False,
                "error_payload": {
                    "type": "order_status",
                    "success": False,
                    "code": "ORDER_NOT_FOUND",
                    "order_id": clean_id,
                    "message": f"Không tìm thấy đơn hàng với mã '{clean_id}'.",
                },
            }

        if str(order.customer_id) != uid_str:
            return {
                "ok": False,
                "error_payload": {
                    "type": "order_status",
                    "success": False,
                    "code": "ORDER_ACCESS_DENIED",
                    "order_id": clean_id,
                    "message": (
                        f"Bạn không có quyền truy cập đơn hàng '{clean_id}' hoặc đơn hàng không thuộc tài khoản của bạn."
                    ),
                },
            }
    else:
        stmt = (
            select(Order)
            .where(Order.customer_id == uid_str)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        res = await db.execute(stmt)
        order = res.scalar_one_or_none()
        if not order:
            return {
                "ok": False,
                "error_payload": {
                    "type": "order_status",
                    "success": False,
                    "code": "ORDER_NOT_FOUND",
                    "message": "Tài khoản của bạn hiện chưa có đơn hàng nào trên hệ thống.",
                    "_actions": [
                        {
                            "type": "CREATE_DELIVERY",
                            "label": "Create delivery",
                            "href": "/customer/create-delivery",
                        }
                    ],
                },
            }

    # Lấy thông tin Hub và Mission liên quan
    origin_hub_obj = await db.get(Hub, order.origin_hub_id) if order.origin_hub_id else None
    dest_hub_obj = await db.get(Hub, order.destination_hub_id) if order.destination_hub_id else None

    mission_stmt = (
        select(Mission)
        .where(Mission.order_id == order.id)
        .order_by(Mission.scheduled_time.desc().nullslast())
    )
    mission_res = await db.execute(mission_stmt)
    mission = mission_res.scalars().first()

    return {
        "ok": True,
        "order": order,
        "origin_hub": origin_hub_obj,
        "dest_hub": dest_hub_obj,
        "mission": mission,
    }


def _hub_dict(hub: Optional[Hub]) -> Optional[Dict[str, Any]]:
    if not hub:
        return None
    return {
        "id": hub.id,
        "code": hub.code,
        "name": hub.name,
        "address": hub.address,
    }


# ==============================================================================
# NHÓM 1: PUBLIC / REFERENCE TOOLS (8 TOOLS)
# ==============================================================================

async def tool_get_package_rules(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """1. Lấy quy định về danh mục hàng hóa, kích thước và cân nặng tối đa."""
    res = CustomerReferenceService.get_package_rules()
    res["success"] = True
    res["_actions"] = [
        {
            "type": "OPEN_PRICING",
            "label": "View pricing & package rules",
            "href": "/customer/pricing",
        }
    ]
    return res


async def tool_recommend_package_size(
    db: AsyncSession,
    weight_kg: float,
    length_cm: Optional[float] = None,
    width_cm: Optional[float] = None,
    height_cm: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    """2. Tư vấn chọn size kiện hàng (S, M, L, XL) theo cân nặng và kích thước."""
    res = CustomerReferenceService.recommend_package_size(
        weight_kg=float(weight_kg),
        length_cm=float(length_cm) if length_cm is not None else None,
        width_cm=float(width_cm) if width_cm is not None else None,
        height_cm=float(height_cm) if height_cm is not None else None,
    )
    if res.get("success"):
        res["_actions"] = [
            {
                "type": "CREATE_DELIVERY",
                "label": "Create delivery",
                "href": "/customer/create-delivery",
                "payload": {
                    "payload_kg": float(weight_kg),
                    "package_size": res.get("recommended_size"),
                },
            }
        ]
    else:
        res["_actions"] = [
            {
                "type": "OPEN_PRICING",
                "label": "View package limits",
                "href": "/customer/pricing",
            }
        ]
    return res


async def tool_get_pricing_rules(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """3. Lấy bảng giá cơ bản (S/M/L/XL) và công thức tính phụ phí."""
    res = CustomerReferenceService.get_pricing_rules()
    res["success"] = True
    res["_actions"] = [
        {
            "type": "OPEN_PRICING",
            "label": "View pricing details",
            "href": "/customer/pricing",
        }
    ]
    return res


async def tool_estimate_shipping_fee(
    db: AsyncSession,
    origin_hub_name: str,
    destination_hub_name: str,
    payload_kg: float = 1.0,
    delivery_mode: str = "SCHEDULED",
    package_type: str = "standard",
    has_insurance: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """4. Ước tính cước phí vận chuyển chính thức giữa 2 trạm Hub."""
    weight = float(payload_kg) if payload_kg is not None else 1.0
    if weight <= 0 or weight > 10.0:
        return {
            "type": "shipping_estimate",
            "success": False,
            "code": "INVALID_PACKAGE_WEIGHT",
            "payload_kg": weight,
            "max_allowed_kg": 10.0,
            "message": (
                f"Khối lượng kiện hàng ({weight} kg) không hợp lệ. "
                "Hệ thống DroneOptAI chỉ nhận kiện hàng từ 0.1 kg đến tối đa 10.0 kg (Size XL)."
            ),
        }

    result = await db.execute(select(Hub).where(Hub.status.ilike("active")))
    all_hubs = result.scalars().all()
    if not all_hubs:
        result = await db.execute(select(Hub))
        all_hubs = result.scalars().all()

    def _match_hub(query_name: str) -> Optional[Hub]:
        q_norm = _strip_accents(query_name.strip())
        for h in all_hubs:
            h_name = _strip_accents(h.name or "")
            h_code = _strip_accents(h.code or "")
            h_addr = _strip_accents(h.address or "")
            if q_norm in h_name or h_name in q_norm or (h_code and q_norm == h_code) or q_norm in h_addr:
                return h
        return None

    orig_hub = _match_hub(origin_hub_name)
    dest_hub = _match_hub(destination_hub_name)

    if not orig_hub or not dest_hub:
        hub_names = [f"{h.name} ({h.address or 'N/A'})" for h in all_hubs]
        return {
            "type": "shipping_estimate",
            "success": False,
            "code": "HUB_NOT_FOUND",
            "message": "Không tìm thấy trạm Hub khớp với tên bạn cung cấp.",
            "available_hubs": hub_names,
        }

    if orig_hub.id == dest_hub.id:
        return {
            "type": "shipping_estimate",
            "success": False,
            "code": "SAME_ORIGIN_DESTINATION_HUB",
            "message": "Trạm Hub gửi và trạm Hub nhận không được trùng nhau.",
        }

    dist_km = haversine_distance(
        orig_hub.latitude or 0.0,
        orig_hub.longitude or 0.0,
        dest_hub.latitude or 0.0,
        dest_hub.longitude or 0.0,
    )

    mode_str = (delivery_mode or "SCHEDULED").upper()
    mode_enum = DeliveryMode.EXPRESS if "EXPRESS" in mode_str else DeliveryMode.SCHEDULED

    core_fee, service_fee, total_fee, fee_details = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=weight,
        package_type=package_type or "standard",
        delivery_mode=mode_enum,
        has_insurance=bool(has_insurance),
    )

    energy_pred = predict_flight_energy(dist_km, weight)
    energy_wh = round(energy_pred.get("energy_wh", 0.0), 2)

    size_rec = CustomerReferenceService.recommend_package_size(weight_kg=weight)
    pkg_size = size_rec.get("recommended_size", "M")

    return {
        "type": "shipping_estimate",
        "success": True,
        "origin_hub": _hub_dict(orig_hub),
        "destination_hub": _hub_dict(dest_hub),
        "distance_km": round(dist_km, 2),
        "payload_kg": weight,
        "package_size": pkg_size,
        "package_type": package_type or "standard",
        "delivery_mode": mode_enum.value,
        "has_insurance": bool(has_insurance),
        "predicted_energy_wh": energy_wh,
        "core_fee": round(core_fee),
        "service_fee": round(service_fee),
        "total_fee": round(total_fee),
        "total_fee_formatted": f"{int(round(total_fee)):,} VNĐ",
        "fee_breakdown": {
            "base_fee": round(fee_details["base_fee"]),
            "distance_fee": round(fee_details["distance_fee"]),
            "payload_fee": round(fee_details["payload_fee"]),
            "core_fee": round(core_fee),
            "fragile_surcharge": round(fee_details["fragile_surcharge"]),
            "express_surcharge": round(fee_details["express_surcharge"]),
            "insurance_fee": round(fee_details["insurance_fee"]),
        },
        "_actions": [
            {
                "type": "CREATE_DELIVERY",
                "label": "Create delivery",
                "href": "/customer/create-delivery",
                "payload": {
                    "origin_hub_id": orig_hub.id,
                    "destination_hub_id": dest_hub.id,
                    "payload_kg": weight,
                    "package_size": pkg_size,
                    "package_type": package_type or "standard",
                    "delivery_mode": mode_enum.value,
                    "has_insurance": bool(has_insurance),
                },
            }
        ],
    }


async def tool_get_delivery_modes(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """5. Lấy thông tin so sánh chế độ giao Scheduled và Express."""
    res = CustomerReferenceService.get_delivery_modes()
    res["success"] = True
    res["_actions"] = [
        {
            "type": "OPEN_PRICING",
            "label": "View delivery modes",
            "href": "/customer/pricing",
        }
    ]
    return res


async def tool_list_hubs(db: AsyncSession, **kwargs) -> Dict[str, Any]:
    """6. Lấy danh sách các trạm Hub giao nhận đang hoạt động."""
    result = await db.execute(select(Hub).where(Hub.status.ilike("active")))
    hubs = result.scalars().all()
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()

    hub_list = [
        {
            "id": h.id,
            "code": h.code,
            "name": h.name,
            "address": h.address,
            "latitude": h.latitude,
            "longitude": h.longitude,
            "status": h.status or "Active",
        }
        for h in hubs
    ]
    return {
        "type": "hub_list",
        "success": True,
        "total_hubs": len(hub_list),
        "hubs": hub_list,
        "_actions": [
            {
                "type": "CREATE_DELIVERY",
                "label": "Create delivery",
                "href": "/customer/create-delivery",
            }
        ],
    }


async def tool_resolve_hubs_by_area(
    db: AsyncSession,
    origin_area: Optional[str] = None,
    destination_area: Optional[str] = None,
    area_query: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """7. Tìm kiếm trạm Hub theo tên khu vực / quận huyện / địa chỉ."""
    result = await db.execute(select(Hub).where(Hub.status.ilike("active")))
    hubs = result.scalars().all()
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()

    def _search_area(q: Optional[str]) -> List[Dict[str, Any]]:
        if not q:
            return []
        q_norm = _strip_accents(q.strip())
        matched = []
        for h in hubs:
            haystack = _strip_accents(f"{h.name or ''} {h.address or ''} {h.code or ''}")
            if q_norm in haystack or any(tok in haystack for tok in q_norm.split() if len(tok) >= 2):
                matched.append(_hub_dict(h))
        return matched

    origin_matches = _search_area(origin_area)
    dest_matches = _search_area(destination_area)
    general_matches = _search_area(area_query)

    return {
        "type": "hub_detail",
        "success": True,
        "origin_area": origin_area,
        "origin_hubs_matched": origin_matches,
        "destination_area": destination_area,
        "destination_hubs_matched": dest_matches,
        "area_query": area_query,
        "matched_hubs": general_matches,
        "all_available_hubs": [_hub_dict(h) for h in hubs],
    }


async def tool_get_help_policy(
    db: AsyncSession,
    topic: str,
    **kwargs,
) -> Dict[str, Any]:
    """8. Tra cứu hướng dẫn sử dụng và chính sách dịch vụ theo chủ đề."""
    res = CustomerHelpService.get_help_policy(topic)
    res["_actions"] = [
        {
            "type": "OPEN_HELP",
            "label": "View Help & Policies",
            "href": "/customer/help",
        }
    ]
    return res


# ==============================================================================
# NHÓM 2: PERSONAL / AUTHENTICATED TOOLS (10 TOOLS - BẮT BUỘC BEARER JWT)
# ==============================================================================

async def tool_get_my_orders(
    db: AsyncSession,
    limit: int = 5,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """9. Lấy danh sách các đơn hàng gần đây của khách hàng hiện tại."""
    if not current_user_id:
        return {
            "type": "order_list",
            "success": False,
            "code": "ORDER_ACCESS_DENIED",
            "message": "Vui lòng đăng nhập tài khoản để xem danh sách đơn hàng của bạn.",
        }

    safe_limit = min(max(int(limit or 5), 1), 15)
    stmt = (
        select(Order)
        .where(Order.customer_id == str(current_user_id))
        .order_by(Order.created_at.desc())
        .limit(safe_limit)
    )
    res = await db.execute(stmt)
    orders = res.scalars().all()

    hubs_res = await db.execute(select(Hub))
    hub_map = {h.id: h for h in hubs_res.scalars().all()}

    order_items = []
    for o in orders:
        m_stmt = select(Mission).where(Mission.order_id == o.id).order_by(Mission.scheduled_time.desc().nullslast())
        m_res = await db.execute(m_stmt)
        mission = m_res.scalars().first()
        status_info = CustomerStatusService.compute_customer_delivery_status(o, mission)
        order_items.append(
            {
                "order_id": o.id,
                "customer_status": status_info["customer_status"],
                "status_label": status_info["status_label"],
                "status_label_vi": status_info["status_label_vi"],
                "order_status": o.status.value if o.status else "PENDING",
                "payment_status": o.payment_status.value if o.payment_status else "UNPAID",
                "delivery_mode": o.delivery_mode.value if o.delivery_mode else "SCHEDULED",
                "payload_kg": o.payload_kg,
                "delivery_fee": round(o.delivery_fee) if o.delivery_fee else 0,
                "delivery_fee_formatted": f"{int(round(o.delivery_fee)):,} VNĐ" if o.delivery_fee else "0 VNĐ",
                "origin_hub": _hub_dict(hub_map.get(o.origin_hub_id)),
                "destination_hub": _hub_dict(hub_map.get(o.destination_hub_id)),
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
        )

    actions = []
    if order_items:
        actions.append(
            {
                "type": "OPEN_ORDER",
                "label": f"View latest delivery ({order_items[0]['order_id']})",
                "href": f"/customer/deliveries/{order_items[0]['order_id']}",
            }
        )
    else:
        actions.append(
            {
                "type": "CREATE_DELIVERY",
                "label": "Create delivery",
                "href": "/customer/create-delivery",
            }
        )

    return {
        "type": "order_list",
        "success": True,
        "count": len(order_items),
        "orders": order_items,
        "_actions": actions,
    }


async def tool_get_latest_order(
    db: AsyncSession,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """10. Lấy thông tin đơn hàng gần nhất của khách hàng hiện tại."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=None)
    if not found["ok"]:
        return found["error_payload"]

    return await _build_full_order_status_payload(
        order=found["order"],
        origin_hub=found["origin_hub"],
        dest_hub=found["dest_hub"],
        mission=found["mission"],
    )


async def tool_get_order_detail(
    db: AsyncSession,
    order_id: str,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """11. Xem chi tiết một đơn hàng cụ thể (bắt buộc kiểm tra quyền sở hữu)."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=order_id)
    if not found["ok"]:
        return found["error_payload"]

    return await _build_full_order_status_payload(
        order=found["order"],
        origin_hub=found["origin_hub"],
        dest_hub=found["dest_hub"],
        mission=found["mission"],
    )


async def tool_get_customer_delivery_status(
    db: AsyncSession,
    order_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """12. Lấy trạng thái đơn hàng tổng hợp chuẩn Customer-friendly."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=order_id)
    if not found["ok"]:
        return found["error_payload"]

    return await _build_full_order_status_payload(
        order=found["order"],
        origin_hub=found["origin_hub"],
        dest_hub=found["dest_hub"],
        mission=found["mission"],
    )


async def _build_full_order_status_payload(
    order: Order,
    origin_hub: Optional[Hub],
    dest_hub: Optional[Hub],
    mission: Optional[Mission],
) -> Dict[str, Any]:
    status_info = CustomerStatusService.compute_customer_delivery_status(order, mission)
    times_info = CustomerStatusService.compute_handover_and_eta(order, mission)
    pay_status = order.payment_status.value if order.payment_status else "UNPAID"

    actions = [
        {
            "type": "OPEN_ORDER",
            "label": "View delivery",
            "href": f"/customer/deliveries/{order.id}",
        }
    ]
    if pay_status in ("UNPAID", "PENDING") and order.status != OrderStatus.CANCELLED:
        actions.append(
            {
                "type": "OPEN_PAYMENT",
                "label": "Complete payment",
                "href": f"/customer/deliveries/{order.id}",
            }
        )

    return {
        "type": "order_status",
        "success": True,
        "order_id": order.id,
        "customer_status": status_info["customer_status"],
        "status_label": status_info["status_label"],
        "status_label_vi": status_info["status_label_vi"],
        "handoff_state": status_info["handoff_state"],
        "order_status": order.status.value if order.status else "PENDING",
        "payment_status": pay_status,
        "package_label": order.package_label,
        "package_type": order.package_type,
        "payload_kg": order.payload_kg,
        "delivery_mode": order.delivery_mode.value if order.delivery_mode else "SCHEDULED",
        "delivery_fee": round(order.delivery_fee) if order.delivery_fee else 0,
        "delivery_fee_formatted": f"{int(round(order.delivery_fee)):,} VNĐ" if order.delivery_fee else "0 VNĐ",
        "origin_hub": _hub_dict(origin_hub),
        "destination_hub": _hub_dict(dest_hub),
        "handover_deadline": times_info["handover_deadline"],
        "estimated_departure": times_info["estimated_departure"],
        "estimated_arrival": times_info["estimated_arrival"],
        "origin_received_at": times_info["origin_received_at"],
        "destination_received_at": times_info["destination_received_at"],
        "mission_id": mission.id if mission else None,
        "drone_id": mission.drone_id if mission else None,
        "_actions": actions,
    }


async def tool_get_payment_status(
    db: AsyncSession,
    order_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """13. Kiểm tra trạng thái thanh toán của đơn hàng."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=order_id)
    if not found["ok"]:
        err = found["error_payload"]
        err["type"] = "payment_status"
        return err

    order: Order = found["order"]
    pay_status = order.payment_status.value if order.payment_status else "UNPAID"

    actions = [
        {
            "type": "OPEN_ORDER",
            "label": "View delivery",
            "href": f"/customer/deliveries/{order.id}",
        }
    ]
    if pay_status in ("UNPAID", "PENDING") and order.status != OrderStatus.CANCELLED:
        actions.insert(
            0,
            {
                "type": "OPEN_PAYMENT",
                "label": "Pay now",
                "href": f"/customer/deliveries/{order.id}",
            },
        )

    return {
        "type": "payment_status",
        "success": True,
        "order_id": order.id,
        "payment_status": pay_status,
        "amount": round(order.delivery_fee) if order.delivery_fee else 0,
        "amount_formatted": f"{int(round(order.delivery_fee)):,} VNĐ" if order.delivery_fee else "0 VNĐ",
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "payment_order_code": order.payment_order_code,
        "has_checkout_url": bool(order.payment_checkout_url),
        "_actions": actions,
    }


async def tool_get_handover_status(
    db: AsyncSession,
    order_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """14. Kiểm tra trạng thái bàn giao kiện hàng tại Origin Hub / Destination Hub và hạn chót (Handover Deadline)."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=order_id)
    if not found["ok"]:
        err = found["error_payload"]
        err["type"] = "handover_status"
        return err

    order: Order = found["order"]
    mission: Optional[Mission] = found["mission"]
    status_info = CustomerStatusService.compute_customer_delivery_status(order, mission)
    times_info = CustomerStatusService.compute_handover_and_eta(order, mission)

    return {
        "type": "handover_status",
        "success": True,
        "order_id": order.id,
        "customer_status": status_info["customer_status"],
        "handoff_state": status_info["handoff_state"],
        "origin_hub": _hub_dict(found["origin_hub"]),
        "destination_hub": _hub_dict(found["dest_hub"]),
        "handover_deadline": times_info["handover_deadline"],
        "origin_received": bool(order.origin_received_at),
        "origin_received_at": times_info["origin_received_at"],
        "destination_received": bool(
            order.destination_received_at
            or order.status == OrderStatus.DELIVERED_TO_HUB
            or (mission and mission.arrived_at is not None)
        ),
        "destination_received_at": times_info["destination_received_at"]
        or (mission.arrived_at.isoformat() if (mission and mission.arrived_at) else None),
        "_actions": [
            {
                "type": "OPEN_ORDER",
                "label": "View delivery",
                "href": f"/customer/deliveries/{order.id}",
            }
        ],
    }


async def tool_get_delivery_eta(
    db: AsyncSession,
    order_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """15. Kiểm tra thời gian dự kiến khởi hành và đến nơi (ETA) từ dữ liệu thực."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=order_id)
    if not found["ok"]:
        return found["error_payload"]

    order: Order = found["order"]
    mission: Optional[Mission] = found["mission"]
    status_info = CustomerStatusService.compute_customer_delivery_status(order, mission)
    times_info = CustomerStatusService.compute_handover_and_eta(order, mission)

    has_eta = bool(times_info["estimated_departure"] or times_info["estimated_arrival"])

    return {
        "type": "order_status",
        "success": True,
        "order_id": order.id,
        "customer_status": status_info["customer_status"],
        "status_label_vi": status_info["status_label_vi"],
        "eta_available": has_eta,
        "handover_deadline": times_info["handover_deadline"],
        "estimated_departure": times_info["estimated_departure"],
        "estimated_arrival": times_info["estimated_arrival"],
        "note": (
            "Thời gian dự kiến lấy trực tiếp từ dữ liệu đơn hàng/chuyến bay."
            if has_eta
            else "Đơn hàng này hiện chưa có thời gian dự kiến đến (ETA) được xác nhận trên hệ thống."
        ),
        "_actions": [
            {
                "type": "OPEN_ORDER",
                "label": "View delivery",
                "href": f"/customer/deliveries/{order.id}",
            }
        ],
    }


async def tool_check_cancellation_eligibility(
    db: AsyncSession,
    order_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """16. Kiểm tra xem đơn hàng có đủ điều kiện hủy hay không dựa trên business rule thực tế."""
    found = await _find_customer_order(db, current_user_id=current_user_id, order_id=order_id)
    if not found["ok"]:
        return found["error_payload"]

    order: Order = found["order"]
    mission: Optional[Mission] = found["mission"]
    status_info = CustomerStatusService.compute_customer_delivery_status(order, mission)

    if order.status == OrderStatus.CANCELLED:
        eligible = False
        reason = "Đơn hàng này đã ở trạng thái Đã hủy (CANCELLED) trước đó."
    elif order.status != OrderStatus.PENDING:
        eligible = False
        reason = f"Đơn hàng đang ở trạng thái '{order.status.value}', không thể hủy."
    elif order.origin_received_at is not None:
        eligible = False
        reason = "Kiện hàng đã được bàn giao tại Origin Hub nên không thể tự hủy trực tiếp."
    elif mission and mission.status in (
        MissionStatus.IN_PROGRESS,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
        MissionStatus.COMPLETED,
        MissionStatus.MISSION_COMPLETED,
    ):
        eligible = False
        reason = f"Chuyến bay ({mission.id}) đã khởi hành hoặc hoàn tất nên không thể hủy."
    else:
        eligible = True
        reason = "Đơn hàng đang ở trạng thái PENDING và chưa bàn giao kiện hàng tại Origin Hub nên có thể yêu cầu hủy."

    refund_policy_note = (
        "Lưu ý: Chính sách hoàn tiền chi tiết đối với đơn đã thanh toán (PAID) hiện chưa được công bố chính thức trên hệ thống (POLICY_NOT_DEFINED)."
        if order.payment_status == PaymentStatus.PAID
        else None
    )

    return {
        "type": "order_status",
        "success": True,
        "order_id": order.id,
        "customer_status": status_info["customer_status"],
        "payment_status": order.payment_status.value if order.payment_status else "UNPAID",
        "can_cancel": eligible,
        "reason": reason,
        "refund_policy_code": "POLICY_NOT_DEFINED" if order.payment_status == PaymentStatus.PAID else None,
        "refund_policy_note": refund_policy_note,
        "_actions": [
            {
                "type": "OPEN_ORDER",
                "label": "View delivery",
                "href": f"/customer/deliveries/{order.id}",
            }
        ],
    }


async def tool_get_my_notifications(
    db: AsyncSession,
    limit: int = 5,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """17. Lấy danh sách thông báo mới nhất của khách hàng hiện tại."""
    if not current_user_id:
        return {
            "type": "notification_list",
            "success": False,
            "code": "ORDER_ACCESS_DENIED",
            "message": "Vui lòng đăng nhập để xem thông báo của bạn.",
        }

    safe_limit = min(max(int(limit or 5), 1), 20)
    uid_str = str(current_user_id)

    unread_stmt = select(Notification).where(
        and_(Notification.user_id == uid_str, Notification.is_read == False)  # noqa: E712
    )
    unread_res = await db.execute(unread_stmt)
    unread_count = len(unread_res.scalars().all())

    stmt = (
        select(Notification)
        .where(Notification.user_id == uid_str)
        .order_by(Notification.created_at.desc())
        .limit(safe_limit)
    )
    res = await db.execute(stmt)
    notifs = res.scalars().all()

    items = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "notification_type": n.notifications_type.value if n.notifications_type else None,
            "is_read": n.is_read,
            "mission_id": n.mission_id,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifs
    ]

    return {
        "type": "notification_list",
        "success": True,
        "unread_count": unread_count,
        "notifications": items,
        "_actions": [
            {
                "type": "OPEN_NOTIFICATIONS",
                "label": "View notifications",
                "href": "/customer/notifications",
            }
        ],
    }


async def tool_get_profile(
    db: AsyncSession,
    current_user_id: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """18. Lấy thông tin hồ sơ tài khoản của khách hàng hiện tại."""
    if not current_user_id:
        return {
            "type": "profile_info",
            "success": False,
            "code": "ORDER_ACCESS_DENIED",
            "message": "Vui lòng đăng nhập để xem thông tin tài khoản.",
        }

    user = await get_user_by_id(db, str(current_user_id))
    if not user:
        return {
            "type": "profile_info",
            "success": False,
            "code": "USER_NOT_FOUND",
            "message": "Không tìm thấy thông tin người dùng.",
        }

    return {
        "type": "profile_info",
        "success": True,
        "profile": {
            "full_name": user.full_name,
            "username": getattr(user, "username", None),
            "email": user.email,
            "phone": getattr(user, "phone", None),
            "is_active": user.is_active,
        },
    }


# ==============================================================================
# SCHEMA KHAI BÁO VỚI GEMINI (18 TOOLS)
# ==============================================================================
def get_customer_gemini_tools() -> list:
    if types is None:
        return []
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="get_package_rules",
                    description="Lấy quy định về 8 danh mục hàng hóa và 4 kích thước kiện hàng (S, M, L, XL: cân nặng tối đa kg, kích thước cm).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
                types.FunctionDeclaration(
                    name="recommend_package_size",
                    description="Tư vấn chọn size kiện hàng (S, M, L, XL) dựa trên khối lượng (kg) và kích thước (cm).",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "weight_kg": types.Schema(type=types.Type.NUMBER, description="Khối lượng kiện hàng (kg)."),
                            "length_cm": types.Schema(type=types.Type.NUMBER, description="Chiều dài (cm, tùy chọn)."),
                            "width_cm": types.Schema(type=types.Type.NUMBER, description="Chiều rộng (cm, tùy chọn)."),
                            "height_cm": types.Schema(type=types.Type.NUMBER, description="Chiều cao (cm, tùy chọn)."),
                        },
                        required=["weight_kg"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_pricing_rules",
                    description="Lấy bảng giá cơ bản theo từng size (S, M, L, XL), công thức Core Fee và tỷ lệ % phụ phí (Fragile +15%, Insurance +10%, Express +25%, Scheduled 0%).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
                types.FunctionDeclaration(
                    name="estimate_shipping_fee",
                    description="Tính toán báo giá cước phí giao hàng và năng lượng tiêu thụ giữa 2 trạm Hub.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "origin_hub_name": types.Schema(type=types.Type.STRING, description="Tên hoặc khu vực trạm Hub gửi."),
                            "destination_hub_name": types.Schema(type=types.Type.STRING, description="Tên hoặc khu vực trạm Hub nhận."),
                            "payload_kg": types.Schema(type=types.Type.NUMBER, description="Khối lượng kiện hàng (kg, tối đa 10kg)."),
                            "delivery_mode": types.Schema(type=types.Type.STRING, description="'SCHEDULED' hoặc 'EXPRESS'."),
                            "package_type": types.Schema(type=types.Type.STRING, description="Loại hàng: 'standard', 'fragile', 'food', 'documents', 'electronics', 'medical', 'clothing', 'household', 'other'."),
                            "has_insurance": types.Schema(type=types.Type.BOOLEAN, description="Có mua bảo hiểm kiện hàng (+10% Core Fee) không."),
                        },
                        required=["origin_hub_name", "destination_hub_name"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_delivery_modes",
                    description="Giải thích và so sánh 2 hình thức giao hàng: Scheduled (0% phụ phí) và Express (+25% Core Fee).",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
                types.FunctionDeclaration(
                    name="list_hubs",
                    description="Lấy danh sách tất cả các trạm Hub giao nhận của DroneOptAI.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
                types.FunctionDeclaration(
                    name="resolve_hubs_by_area",
                    description="Tìm kiếm trạm Hub theo tên khu vực, quận/huyện hoặc địa chỉ.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "origin_area": types.Schema(type=types.Type.STRING, description="Khu vực gửi (tùy chọn)."),
                            "destination_area": types.Schema(type=types.Type.STRING, description="Khu vực nhận (tùy chọn)."),
                            "area_query": types.Schema(type=types.Type.STRING, description="Từ khóa khu vực cần tìm Hub (tùy chọn)."),
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_help_policy",
                    description=(
                        "Tra cứu hướng dẫn sử dụng hoặc chính sách của hệ thống theo chủ đề: "
                        "'order_creation', 'hub_to_hub', 'package_rules', 'pricing', 'delivery_modes', "
                        "'handover_deadline', 'payment', 'fragile', 'insurance', 'cancellation', "
                        "'notifications', 'collection', hoặc các chủ đề như 'prohibited_items', "
                        "'insurance_coverage', 'hub_operating_hours', 'recipient_verification', 'express_sla', 'support_contact'."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "topic": types.Schema(type=types.Type.STRING, description="Mã chủ đề cần tra cứu.")
                        },
                        required=["topic"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_my_orders",
                    description="Lấy danh sách các đơn hàng gần đây của khách hàng đang đăng nhập.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "limit": types.Schema(type=types.Type.INTEGER, description="Số đơn tối đa cần lấy (mặc định 5).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_latest_order",
                    description="Lấy chi tiết và trạng thái đơn hàng gần nhất của khách hàng đang đăng nhập.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
                types.FunctionDeclaration(
                    name="get_order_detail",
                    description="Xem chi tiết một đơn hàng cụ thể theo mã đơn (order_id). Chỉ trả về nếu đơn thuộc về khách hàng đang đăng nhập.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng (VD: ORD-0001).")
                        },
                        required=["order_id"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_customer_delivery_status",
                    description="Kiểm tra trạng thái giao hàng tổng hợp của đơn hàng (nếu không truyền order_id sẽ lấy đơn gần nhất).",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng (tùy chọn).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_payment_status",
                    description="Kiểm tra trạng thái thanh toán (UNPAID, PENDING, PAID, CANCELLED) của đơn hàng.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng (tùy chọn, mặc định lấy đơn gần nhất).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_handover_status",
                    description="Kiểm tra trạng thái bàn giao kiện hàng tại Origin Hub, hạn chót (handover_deadline) và trạng thái hàng đến Destination Hub.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng (tùy chọn, mặc định lấy đơn gần nhất).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_delivery_eta",
                    description="Kiểm tra thời gian dự kiến khởi hành và đến nơi (ETA) của đơn hàng.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng (tùy chọn, mặc định lấy đơn gần nhất).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="check_cancellation_eligibility",
                    description="Kiểm tra xem đơn hàng có đủ điều kiện hủy hay không.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "order_id": types.Schema(type=types.Type.STRING, description="Mã đơn hàng (tùy chọn, mặc định lấy đơn gần nhất).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_my_notifications",
                    description="Xem danh sách các thông báo mới của tài khoản khách hàng.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "limit": types.Schema(type=types.Type.INTEGER, description="Số lượng thông báo cần lấy (mặc định 5).")
                        },
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_profile",
                    description="Xem thông tin hồ sơ tài khoản khách hàng đang đăng nhập.",
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
            ]
        )
    ]


CUSTOMER_TOOLS_MAP = {
    # 8 Public / Reference tools
    "get_package_rules": tool_get_package_rules,
    "recommend_package_size": tool_recommend_package_size,
    "get_pricing_rules": tool_get_pricing_rules,
    "estimate_shipping_fee": tool_estimate_shipping_fee,
    "get_delivery_modes": tool_get_delivery_modes,
    "list_hubs": tool_list_hubs,
    "resolve_hubs_by_area": tool_resolve_hubs_by_area,
    "get_help_policy": tool_get_help_policy,
    # 10 Personal / Authenticated tools
    "get_my_orders": tool_get_my_orders,
    "get_latest_order": tool_get_latest_order,
    "get_order_detail": tool_get_order_detail,
    "get_customer_delivery_status": tool_get_customer_delivery_status,
    "get_payment_status": tool_get_payment_status,
    "get_handover_status": tool_get_handover_status,
    "get_delivery_eta": tool_get_delivery_eta,
    "check_cancellation_eligibility": tool_check_cancellation_eligibility,
    "get_my_notifications": tool_get_my_notifications,
    "get_profile": tool_get_profile,
    # Aliases giữ tương thích ngược
    "lookup_order": tool_get_order_detail,
    "get_my_recent_orders": tool_get_my_orders,
}
