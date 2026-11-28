"""
app/modules/missions/compensation_service.py
────────────────────────────────────────────
Nghiệp vụ Mục 6:
1. Ràng buộc lý do khi Operator chọn tuyến bay khác Delivery Mode (đặc biệt EXPRESS nhưng không chọn FAST).
2. Tự động chụp Open-Meteo Weather Snapshot lưu vào `mission.checkpoints_json` khi chọn WEATHER_SAFETY.
3. Đánh giá trễ hạn SLA khi giao tới Hub đích (`actual_arrival > estimated_window_end`) và tính đền bù:
   - WEATHER_SAFETY (có dữ liệu thời tiết minh chứng): Miễn trừ lỗi Operator, hoàn 100% phụ phí Express (25% core fee).
   - OPERATOR_DECISION / RESOURCE_CONSTRAINT: Ghi nhận vi phạm SLA của Operator, hoàn 100% phụ phí Express + Partial Refund 20% core fee.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import get_vn_datetime, get_vn_naive_datetime
from app.modules.ai_predictions.service import haversine_distance
from app.modules.missions.models import (
    DeliveryMode,
    Mission,
    Order,
    PaymentStatus,
    RefundStatus,
)
from app.modules.system.models import ActivityLog, Hub, Notification, NotificationType
from app.shared.weather import evaluate_weather_safety, fetch_weather

VALID_ROUTE_OVERRIDE_REASONS = (
    "WEATHER_SAFETY",
    "OPERATOR_DECISION",
    "RESOURCE_CONSTRAINT",
)

PARTIAL_REFUND_CORE_RATE = 0.20  # 20% core fee khi lỗi Operator chọn sai route gây trễ hạn


def resolve_strategy_code(selected_schema: Any = None, route_id: Optional[str] = None) -> str:
    """Chuẩn hóa mã chiến lược tuyến bay (FAST | ECO | DETOUR | SAFE | DISPATCH)."""
    if selected_schema is not None:
        sc = getattr(selected_schema, "strategy_code", None)
        if sc:
            upper_sc = str(sc).strip().upper()
            return "ECO" if upper_sc == "ENG" else upper_sc
        rid = getattr(selected_schema, "routeId", None) or route_id
    else:
        rid = route_id

    if rid:
        suffix = str(rid).strip().rsplit("-", 1)[-1].upper()
        if suffix == "ENG":
            return "ECO"
        if suffix in ("FAST", "ECO", "DETOUR", "SAFE", "DISPATCH"):
            return suffix
    return "UNKNOWN"


async def capture_weather_snapshot(
    lat: Optional[float],
    lng: Optional[float],
    fallback_weather: Any = None,
) -> dict[str, Any]:
    """
    Chụp snapshot thời tiết thực tế từ Open-Meteo tại tọa độ (lat, lng) làm minh chứng.
    Nếu không gọi được Open-Meteo ngoài mạng, fallback sang dữ liệu thời tiết vừa phân tích trên route.
    """
    now_iso = get_vn_datetime().isoformat()
    w_eval: Optional[dict[str, Any]] = None
    source = "Open-Meteo"

    if lat is not None and lng is not None:
        w_raw = await fetch_weather(float(lat), float(lng), use_cache=False)
        if w_raw is not None:
            w_eval = evaluate_weather_safety(w_raw, altitude_m=50.0)

    if w_eval is None and fallback_weather is not None:
        source = "Open-Meteo (route-analysis-cache)"
        if isinstance(fallback_weather, dict):
            w_eval = dict(fallback_weather)
        elif hasattr(fallback_weather, "model_dump"):
            w_eval = fallback_weather.model_dump()

    if w_eval is None:
        w_eval = {
            "temperature_c": None,
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "precipitation_mm": None,
            "humidity_pct": None,
            "condition_label": "Weather data unavailable",
            "is_bad_weather": False,
            "warning_message": None,
            "weather_summary": "Weather snapshot unavailable at creation time",
        }

    return {
        "source": source,
        "timestamp": now_iso,
        "latitude": round(float(lat), 6) if lat is not None else None,
        "longitude": round(float(lng), 6) if lng is not None else None,
        "temperature_c": w_eval.get("temperature_c"),
        "wind_speed": w_eval.get("wind_speed_ms"),
        "wind_speed_ms": w_eval.get("wind_speed_ms"),
        "wind_gust": w_eval.get("wind_gust_ms"),
        "wind_gust_ms": w_eval.get("wind_gust_ms"),
        "precipitation": w_eval.get("precipitation_mm"),
        "precipitation_mm": w_eval.get("precipitation_mm"),
        "humidity_pct": w_eval.get("humidity_pct"),
        "condition_label": w_eval.get("condition_label"),
        "is_bad_weather": bool(w_eval.get("is_bad_weather", False)),
        "warning_message": w_eval.get("warning_message"),
        "weather_summary": w_eval.get("weather_summary"),
    }


async def validate_and_build_route_override_entry(
    *,
    orders_list: list[Order],
    selected_schema: Any,
    route_id: Optional[str],
    reason_type: Optional[str],
    note: Optional[str],
    operator_id: str,
    origin_hub: Optional[Hub] = None,
    dest_hub: Optional[Hub] = None,
) -> Optional[dict[str, Any]]:
    """
    Kiểm tra ràng buộc chọn Route khác Delivery Mode:
    - Nếu có đơn EXPRESS nhưng Operator chọn tuyến không phải FAST -> bắt buộc `reason_type`.
    - Nếu chọn `WEATHER_SAFETY` -> chụp Open-Meteo snapshot lưu làm minh chứng.
    """
    strategy_code = resolve_strategy_code(selected_schema, route_id)
    express_orders = [
        o for o in orders_list
        if (
            o.delivery_mode == DeliveryMode.EXPRESS
            or str(getattr(o.delivery_mode, "value", o.delivery_mode) or "").upper() == "EXPRESS"
        )
    ]

    clean_reason = str(reason_type).strip().upper() if reason_type else None
    if clean_reason and clean_reason not in VALID_ROUTE_OVERRIDE_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid route_override_reason_type '{reason_type}'. Must be one of: {VALID_ROUTE_OVERRIDE_REASONS}.",
        )

    is_express_mismatch = bool(express_orders and strategy_code != "FAST")
    if is_express_mismatch and not clean_reason:
        express_ids = ", ".join(o.id for o in express_orders)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order(s) [{express_ids}] use EXPRESS delivery mode, but selected route '{strategy_code}' is not FAST. "
                f"Please provide 'route_override_reason_type' ({' | '.join(VALID_ROUTE_OVERRIDE_REASONS)}) and optional 'route_override_note'."
            ),
        )

    if not is_express_mismatch and not clean_reason:
        return None

    weather_evidence: Optional[dict[str, Any]] = None
    if clean_reason == "WEATHER_SAFETY":
        snap_lat = None
        snap_lng = None
        if origin_hub and dest_hub and origin_hub.latitude is not None and dest_hub.latitude is not None:
            snap_lat = (float(origin_hub.latitude) + float(dest_hub.latitude)) / 2.0
            snap_lng = (float(origin_hub.longitude) + float(dest_hub.longitude)) / 2.0
        elif origin_hub and origin_hub.latitude is not None:
            snap_lat = float(origin_hub.latitude)
            snap_lng = float(origin_hub.longitude)
        fallback_w = getattr(selected_schema, "weather", None) if selected_schema is not None else None
        weather_evidence = await capture_weather_snapshot(snap_lat, snap_lng, fallback_weather=fallback_w)

    return {
        "type": "route_override",
        "delivery_mode": "EXPRESS" if express_orders else str(
            getattr(orders_list[0].delivery_mode, "value", orders_list[0].delivery_mode) if orders_list else "NORMAL"
        ),
        "affected_express_order_ids": [o.id for o in express_orders],
        "selected_route_id": getattr(selected_schema, "routeId", None) or route_id,
        "selected_strategy_code": strategy_code,
        "reason_type": clean_reason,
        "note": (note or "").strip() or None,
        "operator_id": operator_id,
        "recorded_at": get_vn_datetime().isoformat(),
        "weather_evidence": weather_evidence,
    }


def compute_express_and_core_fee(
    order: Order,
    origin_hub: Optional[Hub] = None,
    dest_hub: Optional[Hub] = None,
) -> tuple[float, float]:
    """
    Tính chính xác `core_fee` và `express_surcharge` (25% core fee) khớp với `calculate_delivery_cost`.
    Nếu `order.delivery_fee` đã lưu có chênh lệch làm tròn, scale theo đúng tổng phí thực thu của đơn.
    """
    if (
        origin_hub
        and dest_hub
        and origin_hub.latitude is not None
        and origin_hub.longitude is not None
        and dest_hub.latitude is not None
        and dest_hub.longitude is not None
    ):
        dist_km = haversine_distance(
            float(origin_hub.latitude),
            float(origin_hub.longitude),
            float(dest_hub.latitude),
            float(dest_hub.longitude),
        )
    else:
        dist_km = 8.0

    payload_kg = float(order.payload_kg or 1.0)
    if payload_kg <= 1:
        base_fee, rate_per_km, rate_per_kg = 20000.0, 4000.0, 3000.0
    elif payload_kg <= 3:
        base_fee, rate_per_km, rate_per_kg = 30000.0, 5000.0, 4000.0
    elif payload_kg <= 5:
        base_fee, rate_per_km, rate_per_kg = 45000.0, 6000.0, 5000.0
    else:
        base_fee, rate_per_km, rate_per_kg = 65000.0, 8000.0, 6000.0

    core_fee = base_fee + dist_km * rate_per_km + payload_kg * rate_per_kg
    is_fragile = bool(order.package_type and str(order.package_type).strip().lower() == "fragile")
    fragile_surcharge = core_fee * 0.15 if is_fragile else 0.0
    is_express = (
        order.delivery_mode == DeliveryMode.EXPRESS
        or str(getattr(order.delivery_mode, "value", order.delivery_mode) or "").upper() == "EXPRESS"
    )
    express_surcharge = core_fee * 0.25 if is_express else 0.0
    expected_total = core_fee + fragile_surcharge + express_surcharge

    actual_fee = float(order.delivery_fee or 0.0)
    if actual_fee > 0 and expected_total > 0:
        scale = actual_fee / expected_total
        # Nếu khách có bảo hiểm (+10% core_fee), kiểm tra tỷ lệ khớp
        expected_with_ins = expected_total + core_fee * 0.10
        if abs(actual_fee - expected_with_ins) < abs(actual_fee - expected_total):
            scale = actual_fee / expected_with_ins
        core_fee *= scale
        express_surcharge *= scale

    return round(core_fee, 2), round(express_surcharge, 2)


def extract_mission_override_and_compensation(
    mission: Optional[Mission],
    order: Optional[Order] = None,
) -> dict[str, Any]:
    """Trích xuất metadata route_override, weather_evidence và late_delivery_compensation từ mission/order."""
    route_override = None
    late_comp = None
    if mission and isinstance(mission.checkpoints_json, list):
        for entry in mission.checkpoints_json:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "route_override":
                route_override = entry
            elif entry.get("type") == "late_delivery_compensation":
                if order is None or not entry.get("order_id") or entry.get("order_id") == order.id:
                    late_comp = entry

    weather_evidence = route_override.get("weather_evidence") if route_override else None
    if late_comp and late_comp.get("weather_evidence"):
        weather_evidence = late_comp.get("weather_evidence")

    return {
        "route_override": route_override,
        "weather_evidence": weather_evidence,
        "late_delivery_compensation": late_comp,
    }


async def apply_late_delivery_compensation_if_applicable(
    *,
    db: AsyncSession,
    mission: Mission,
    order_obj: Order,
    actual_arrival_at: datetime,
    origin_hub: Optional[Hub] = None,
    dest_hub: Optional[Hub] = None,
) -> Optional[dict[str, Any]]:
    """
    Đánh giá giao trễ hạn so với `order.estimated_window_end` khi đơn EXPRESS bị chuyển route:
    - WEATHER_SAFETY: Miễn trừ lỗi Operator, hoàn 100% phụ phí Express (25% core fee).
    - OPERATOR_DECISION / RESOURCE_CONSTRAINT: Ghi nhận vi phạm SLA Operator, hoàn 100% phụ phí Express + 20% core fee.
    """
    d_mode = order_obj.delivery_mode.value if hasattr(order_obj.delivery_mode, "value") else str(order_obj.delivery_mode or "")
    if d_mode.upper() != "EXPRESS":
        return None
    if not order_obj.estimated_window_end:
        return None

    arr_naive = actual_arrival_at.replace(tzinfo=None) if getattr(actual_arrival_at, "tzinfo", None) else actual_arrival_at
    win_end_naive = (
        order_obj.estimated_window_end.replace(tzinfo=None)
        if getattr(order_obj.estimated_window_end, "tzinfo", None)
        else order_obj.estimated_window_end
    )
    late_minutes = round((arr_naive - win_end_naive).total_seconds() / 60.0, 1)
    if late_minutes <= 0:
        return None

    meta = extract_mission_override_and_compensation(mission, order_obj)
    route_override = meta.get("route_override")
    if not route_override:
        return None

    reason_type = str(route_override.get("reason_type") or "OPERATOR_DECISION").upper()
    weather_evidence = route_override.get("weather_evidence")
    core_fee_val, express_surcharge_val = compute_express_and_core_fee(order_obj, origin_hub, dest_hub)

    if reason_type == "WEATHER_SAFETY":
        operator_sla_violation = False
        express_refund = round(express_surcharge_val, -2) if express_surcharge_val >= 1000 else round(express_surcharge_val, 0)
        partial_refund = 0.0
        total_refund = express_refund
        policy_code = "WEATHER_SAFETY_EXPRESS_REFUND"
        w_sum = (weather_evidence or {}).get("weather_summary") or "Adverse weather recorded by Open-Meteo"
        short_reason = (
            f"Late EXPRESS delivery (+{late_minutes:.0f}m) due to WEATHER_SAFETY ({w_sum[:90]}). "
            f"Operator SLA exempted; 100% Express surcharge refunded."
        )
    else:
        operator_sla_violation = True
        express_refund = round(express_surcharge_val, -2) if express_surcharge_val >= 1000 else round(express_surcharge_val, 0)
        raw_partial = core_fee_val * PARTIAL_REFUND_CORE_RATE
        partial_refund = round(raw_partial, -2) if raw_partial >= 1000 else round(raw_partial, 0)
        total_refund = express_refund + partial_refund
        if order_obj.delivery_fee and total_refund > float(order_obj.delivery_fee):
            total_refund = float(order_obj.delivery_fee)
        policy_code = "OPERATOR_ROUTE_OVERRIDE_SLA_VIOLATION"
        short_reason = (
            f"Late EXPRESS delivery (+{late_minutes:.0f}m) due to {reason_type} route override. "
            f"Operator SLA violation recorded; 100% Express surcharge + 20% core fee partial refund."
        )

    # Tránh ghi đè nếu đơn đã hoàn tất refund trước đó
    if (
        order_obj.payment_status == PaymentStatus.PAID
        and order_obj.refund_status != RefundStatus.COMPLETED
        and total_refund > 0
    ):
        order_obj.refund_amount = float(total_refund)
        order_obj.refund_reason = short_reason[:255]
        order_obj.refund_status = RefundStatus.PENDING

    comp_entry = {
        "type": "late_delivery_compensation",
        "order_id": order_obj.id,
        "mission_id": mission.id,
        "policy_code": policy_code,
        "reason_type": reason_type,
        "operator_id": route_override.get("operator_id") or mission.operator_id,
        "operator_sla_violation": operator_sla_violation,
        "late_minutes": late_minutes,
        "estimated_window_end": win_end_naive.isoformat(),
        "actual_arrival_at": arr_naive.isoformat(),
        "core_fee": core_fee_val,
        "express_surcharge_refund": express_refund,
        "partial_delivery_refund": partial_refund,
        "total_refund_amount": total_refund,
        "refund_reason": short_reason,
        "weather_evidence": weather_evidence,
        "recorded_at": get_vn_datetime().isoformat(),
    }

    existing_cps = [
        cp for cp in (mission.checkpoints_json or [])
        if not (isinstance(cp, dict) and cp.get("type") == "late_delivery_compensation" and cp.get("order_id") == order_obj.id)
    ]
    existing_cps.append(comp_entry)
    mission.checkpoints_json = existing_cps

    db.add(ActivityLog(
        user_id=route_override.get("operator_id") or mission.operator_id,
        role="OPERATOR",
        action="OPERATOR_SLA_VIOLATION" if operator_sla_violation else "LATE_DELIVERY_WEATHER_EXEMPTION",
        resource_type="order",
        resource_id=order_obj.id,
        description=short_reason,
        status="warning" if operator_sla_violation else "success",
        details_json=comp_entry,
        created_at=get_vn_datetime(),
    ))

    now_tz = get_vn_datetime()
    if order_obj.customer_id and total_refund > 0:
        db.add(Notification(
            user_id=str(order_obj.customer_id),
            title=f"Late Delivery Compensation — Order #{order_obj.id}",
            message=(
                f"Your EXPRESS order #{order_obj.id} arrived {late_minutes:.0f} min after the committed window "
                f"({reason_type}). Refund pending: {int(total_refund):,} VND."
            ),
            notifications_type=NotificationType.PAYMENT,
            reference_id=f"LATE_COMP_{order_obj.id}",
            mission_id=str(mission.id),
            created_at=now_tz,
        ))

    if operator_sla_violation:
        from app.shared.workers import get_admin_ids
        for adm_id in await get_admin_ids(db):
            db.add(Notification(
                user_id=str(adm_id),
                title=f"Operator SLA Violation — Order #{order_obj.id}",
                message=(
                    f"Operator {route_override.get('operator_id') or mission.operator_id} overrode EXPRESS route "
                    f"({reason_type}) causing +{late_minutes:.0f}m delay. Compensation due: {int(total_refund):,} VND."
                ),
                notifications_type=NotificationType.SYSTEM_ALERT,
                reference_id=f"SLA_VIOLATION_{mission.id}",
                mission_id=str(mission.id),
                created_at=now_tz,
            ))

    return comp_entry
