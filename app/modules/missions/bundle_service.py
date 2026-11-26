import copy
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from fastapi import HTTPException

from app.modules.ai_predictions.service import predict_flight_energy
from app.modules.missions.models import Order, OrderStatus, PaymentStatus, Mission, MissionStatus
from app.modules.fleet.models import Drone, DroneStatus, Battery
from app.modules.missions.technician.router import DRONE_BATTERY_COMPATIBILITY
from app.modules.system.models import Hub

# [PERF NOTE] Các in-memory cache (Energy/Compat/Hubs) dùng chung cho 1 worker uvicorn nhằm triệt tiêu
# hàng trăm lần gọi model ML hoặc N+1 query tới Supabase. Không xóa cache hay quay lại query lặp trong vòng lặp.
_FLIGHT_ENERGY_CACHE: dict[tuple, dict] = {}
_FLIGHT_ENERGY_CACHE_MAX = 4096

_COMPAT_MODELS_CACHE: Optional[tuple[float, dict[str, list[str]]]] = None
_COMPAT_MODELS_TTL_SEC = 300.0

_HUBS_CACHE: Optional[tuple[float, list["_CachedHub"]]] = None
_HUBS_CACHE_TTL_SEC = 300.0


@dataclass(frozen=True)
class _CachedHub:
    id: int
    code: Optional[str]
    name: str
    address: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    status: Optional[str]


_ROUTE_INVALIDATION_CALLBACKS: list = []


def register_route_cache_invalidator(fn) -> None:
    if fn not in _ROUTE_INVALIDATION_CALLBACKS:
        _ROUTE_INVALIDATION_CALLBACKS.append(fn)


def invalidate_route_analysis_cache() -> None:
    for fn in _ROUTE_INVALIDATION_CALLBACKS:
        try:
            fn()
        except Exception:
            pass


def invalidate_planning_metadata_caches() -> None:
    global _COMPAT_MODELS_CACHE, _HUBS_CACHE
    _COMPAT_MODELS_CACHE = None
    _HUBS_CACHE = None
    invalidate_route_analysis_cache()


async def get_all_hubs_cached(db: AsyncSession) -> list[_CachedHub]:
    """Fetch all Hubs with a 300s TTL cache (invalidated on admin Hub changes) to avoid repeated remote DB roundtrips."""
    global _HUBS_CACHE
    now_mono = time.monotonic()
    if _HUBS_CACHE is not None and (now_mono - _HUBS_CACHE[0]) <= _HUBS_CACHE_TTL_SEC:
        return _HUBS_CACHE[1]

    res = await db.execute(select(Hub).order_by(Hub.id))
    hubs = [
        _CachedHub(
            id=h.id,
            code=h.code,
            name=h.name,
            address=h.address,
            latitude=h.latitude,
            longitude=h.longitude,
            status=h.status,
        )
        for h in res.scalars().all()
    ]
    _HUBS_CACHE = (now_mono, hubs)
    return hubs


def _weather_cache_key(weather_override: Optional[dict]) -> Optional[tuple]:
    if not weather_override or not isinstance(weather_override, dict):
        return None
    fields = (
        "temperature_c", "temperature", "humidity_pct", "humidity",
        "avg_wind_speed", "wind_speed", "wind_gust_ms", "wind_gust",
        "wind_dir_deg", "wind_direction", "pressure_hpa", "pressure",
        "cloud_cover_pct", "cloud_cover", "precipitation_mm", "precipitation",
    )
    return tuple((k, weather_override.get(k)) for k in fields if weather_override.get(k) is not None)


def predict_flight_energy_cached(
    distance_km: float,
    payload_weight: float,
    speed: float = 10.0,
    altitude: float = 50.0,
    weather_override: Optional[dict] = None,
    bearing_deg: Optional[float] = None,
) -> dict:
    """Memoized wrapper around `predict_flight_energy` returning an isolated copy."""
    key = (
        round(float(distance_km or 0.0), 4),
        round(float(payload_weight or 0.0), 3),
        round(float(speed or 10.0), 2),
        round(float(altitude or 50.0), 1),
        _weather_cache_key(weather_override),
        round(float(bearing_deg) % 360.0, 1) if bearing_deg is not None else None,
    )
    cached = _FLIGHT_ENERGY_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)

    result = predict_flight_energy(
        distance_km=distance_km,
        payload_weight=payload_weight,
        speed=speed,
        altitude=altitude,
        weather_override=weather_override,
        bearing_deg=bearing_deg,
    )
    if len(_FLIGHT_ENERGY_CACHE) >= _FLIGHT_ENERGY_CACHE_MAX:
        _FLIGHT_ENERGY_CACHE.clear()
    _FLIGHT_ENERGY_CACHE[key] = copy.deepcopy(result)
    return result


async def get_compatible_battery_models_map(db: AsyncSession) -> dict[str, list[str]]:
    """Batch-fetch compatible battery models grouped by drone model with short TTL cache."""
    global _COMPAT_MODELS_CACHE
    now_mono = time.monotonic()
    if _COMPAT_MODELS_CACHE is not None and (now_mono - _COMPAT_MODELS_CACHE[0]) <= _COMPAT_MODELS_TTL_SEC:
        return _COMPAT_MODELS_CACHE[1]

    stmt = (
        select(Drone.model, Battery.battery_model)
        .join(Drone, Battery.drone_id == Drone.id)
        .where(
            Drone.model.is_not(None),
            Battery.battery_model.is_not(None),
        )
        .distinct()
    )
    res = await db.execute(stmt)
    compat_map: dict[str, list[str]] = {}
    for drone_model, bat_model in res.all():
        if drone_model and bat_model:
            compat_map.setdefault(drone_model, []).append(bat_model)
    for drone_model, defaults in DRONE_BATTERY_COMPATIBILITY.items():
        if not compat_map.get(drone_model):
            compat_map[drone_model] = list(defaults)
    _COMPAT_MODELS_CACHE = (now_mono, compat_map)
    return compat_map


def resolve_compatible_models_from_map(
    compat_map: dict[str, list[str]],
    drone_model: Optional[str],
) -> list[str]:
    """Lookup compatible battery models for a drone model using a preloaded map."""
    if not drone_model:
        return []
    models = compat_map.get(drone_model)
    if models:
        return models
    return list(DRONE_BATTERY_COMPATIBILITY.get(drone_model, [drone_model]))

ACTIVE_MISSION_STATUSES = [
    MissionStatus.SCHEDULED,
    MissionStatus.FLYING,
]

async def get_active_mission_order_ids(db: AsyncSession) -> set[str]:
    """Lấy danh sách order_id của tất cả các Mission đang hoạt động."""
    res = await db.execute(
        select(Mission.order_id).where(
            Mission.status.in_(ACTIVE_MISSION_STATUSES),
            Mission.order_id.isnot(None)
        )
    )
    return set(res.scalars().all())


def resolve_effective_origin_from_missions(
    order: Optional[Order],
    order_missions: list[Mission],
) -> tuple[Optional[int], bool]:
    """
    [PERF NOTE] Tính Hub xuất phát thực tế trực tiếp từ danh sách Mission đã preload của Order (0 SQL),
    tránh query lại bảng `missions` thêm 1-2 lần như `resolve_effective_origin_hub_id`.
    """
    if not order:
        return None, False

    if order.status == OrderStatus.AT_HUB or order.replan_required_at is not None:
        sorted_ms = sorted(order_missions, key=lambda m: m.id, reverse=True)
        last_inc_mission = next(
            (m for m in sorted_ms if m.status in (MissionStatus.INCIDENT_RETURN, MissionStatus.FAILED)),
            None,
        )
        if (
            last_inc_mission
            and last_inc_mission.destination_hub_id
            and (order.replan_required_at is not None or last_inc_mission.arrival_confirmed_at is not None)
        ):
            newer_completed = any(
                m.id > last_inc_mission.id
                and m.status in ([MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED] + ACTIVE_MISSION_STATUSES)
                for m in sorted_ms
            )
            if not newer_completed:
                if last_inc_mission.destination_hub_id == order.destination_hub_id:
                    return order.origin_hub_id, False
                is_diverted = (last_inc_mission.destination_hub_id != order.origin_hub_id)
                return last_inc_mission.destination_hub_id, is_diverted

    return order.origin_hub_id, False


async def resolve_effective_origin_hub_id(
    db: AsyncSession,
    order: Optional[Order],
) -> tuple[Optional[int], bool]:
    """
    Xác định Hub xuất phát thực tế cho nhiệm vụ bay mà KHÔNG làm thay đổi `order.origin_hub_id` gốc của Customer.
    Nếu đơn hàng đang cần Replan (`replan_required_at != None` hoặc Mission gần nhất kết thúc bằng `INCIDENT_RETURN`/`FAILED`),
    tự động lấy `last_incident_mission.destination_hub_id` (Hub drone vừa đáp khẩn cấp) làm điểm xuất phát cho chuyến bay mới.
    """
    if not order:
        return None, False

    if order.status == OrderStatus.AT_HUB or order.replan_required_at is not None:
        last_inc_res = await db.execute(
            select(Mission)
            .where(
                Mission.order_id == order.id,
                Mission.status.in_([MissionStatus.INCIDENT_RETURN, MissionStatus.FAILED]),
            )
            .order_by(Mission.id.desc())
            .limit(1)
        )
        last_inc_mission = last_inc_res.scalars().first()
        if (
            last_inc_mission
            and last_inc_mission.destination_hub_id
            and (order.replan_required_at is not None or last_inc_mission.arrival_confirmed_at is not None)
        ):
            newer_completed_res = await db.execute(
                select(Mission.id)
                .where(
                    Mission.order_id == order.id,
                    Mission.id > last_inc_mission.id,
                    Mission.status.in_(
                        [
                            MissionStatus.ARRIVAL_COMPLETED,
                            MissionStatus.MISSION_COMPLETED,
                        ] + ACTIVE_MISSION_STATUSES
                    ),
                )
                .limit(1)
            )
            if not newer_completed_res.scalars().first():
                # Bảo vệ: Nếu destination_hub_id của mission sự cố trùng với order.destination_hub_id
                # (ví dụ dữ liệu RTH cũ chưa cập nhật destination_hub_id về hub quay đầu),
                # KHÔNG bao giờ lấy Hub đích làm Hub xuất phát cho chuyến bay mới.
                if last_inc_mission.destination_hub_id == order.destination_hub_id:
                    return order.origin_hub_id, False
                is_diverted = (last_inc_mission.destination_hub_id != order.origin_hub_id)
                return last_inc_mission.destination_hub_id, is_diverted

    return order.origin_hub_id, False

async def get_available_orders_query(
    db: AsyncSession,
    origin_hub_id: int,
    destination_hub_id: int,
    exclude_order_ids: Optional[set[str]] = None,
    require_paid: bool = False,
) -> list[Order]:
    """
    Truy vấn các đơn hàng sẵn sàng gộp tại cùng Trạm gửi và Trạm nhận:
    - Trạng thái: AT_HUB
    - Chưa gắn với Mission đang hoạt động nào.
    - require_paid: Lọc thêm điều kiện thanh toán PAID nếu cần.
    """
    active_order_ids = await get_active_mission_order_ids(db)
    if exclude_order_ids:
        active_order_ids.update(exclude_order_ids)

    conditions = [
        Order.origin_hub_id == origin_hub_id,
        Order.destination_hub_id == destination_hub_id,
        Order.status == OrderStatus.AT_HUB,
    ]
    if require_paid:
        conditions.append(Order.payment_status == PaymentStatus.PAID)

    query = select(Order).where(*conditions).order_by(Order.created_at.asc())
    result = await db.execute(query)
    all_orders = result.scalars().all()
    return [o for o in all_orders if o.id not in active_order_ids]

# ══════════════════════════════════════════════════════════════════════════════
# LUỒNG 1: PRE-ANALYZE CONSOLIDATION (Gom đơn trước khi Phân tích Lộ trình)
# ══════════════════════════════════════════════════════════════════════════════

async def get_pre_analyze_bundle_candidates(
    db: AsyncSession,
    primary_order_id: str,
    require_paid: bool = False,
) -> dict[str, Any]:
    """
    Lấy danh sách các đơn hàng có thể gộp trước với đơn hàng chính.
    Hiển thị tổng tải trọng tiềm năng và gợi ý dòng Drone phù hợp (Swift, Falcon, Eagle).
    """
    clean_id = primary_order_id.strip()
    primary_order = await db.get(Order, clean_id)
    if not primary_order:
        raise HTTPException(status_code=404, detail=f"Order '{primary_order_id}' not found")

    if not primary_order.origin_hub_id or not primary_order.destination_hub_id:
        raise HTTPException(status_code=400, detail="Order is missing origin hub or destination hub information")

    hub_map = {h.id: h for h in await get_all_hubs_cached(db)}
    origin_hub = hub_map.get(primary_order.origin_hub_id)
    dest_hub = hub_map.get(primary_order.destination_hub_id)

    candidates = await get_available_orders_query(
        db=db,
        origin_hub_id=primary_order.origin_hub_id,
        destination_hub_id=primary_order.destination_hub_id,
        exclude_order_ids={primary_order.id},
        require_paid=require_paid,
    )

    candidates_data = []
    for o in candidates:
        weight = float(o.payload_kg or 0.0)
        candidates_data.append({
            "order_id": o.id,
            "package_label": o.package_label,
            "payload_kg": weight,
            "receiver_name": o.receiver_name,
            "receiver_phone": o.receiver_phone,
            "delivery_fee": float(o.delivery_fee or 0.0),
            "payment_status": o.payment_status.value if o.payment_status else "PENDING",
            "is_paid": o.payment_status == PaymentStatus.PAID,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    return {
        "workflow": "PRE_ANALYZE",
        "primary_order": {
            "order_id": primary_order.id,
            "package_label": primary_order.package_label,
            "payload_kg": float(primary_order.payload_kg or 0.0),
            "origin_hub_id": primary_order.origin_hub_id,
            "origin_hub_name": origin_hub.name if origin_hub else None,
            "destination_hub_id": primary_order.destination_hub_id,
            "destination_hub_name": dest_hub.name if dest_hub else None,
            "payment_status": primary_order.payment_status.value if primary_order.payment_status else "PENDING",
            "is_paid": primary_order.payment_status == PaymentStatus.PAID,
            "status": primary_order.status.value if primary_order.status else None,
        },
        "candidate_orders": candidates_data,
        "total_candidates": len(candidates_data),
        "drone_capacity_guidelines": [
            {"tier": "Light", "name_prefix": "Swift", "max_payload_kg": 1.0, "note": "Suitable for 1 lightweight order"},
            {"tier": "Medium", "name_prefix": "Falcon", "max_payload_kg": 5.0, "note": "Suitable for bundling 2–4 orders"},
            {"tier": "Heavy", "name_prefix": "Eagle", "max_payload_kg": 10.0, "note": "Suitable for bundling multiple heavy orders"},
        ]
    }

async def validate_bundle_orders(
    db: AsyncSession,
    order_ids: list[str],
    drone_id: Optional[Union[str, int]] = None,
    strict_payment: bool = False,
) -> dict[str, Any]:
    """
    Xác thực tập hợp đơn hàng định gộp chung một chuyến bay:
    1. Đảm bảo tất cả đơn hàng cùng origin_hub và destination_hub.
    2. Đảm bảo tất cả đơn hàng đang AT_HUB (và PAID nếu strict_payment=True).
    3. Đảm bảo không đơn nào đang nằm trong Mission active khác.
    4. Nếu có chọn Drone: Kiểm tra tổng tải trọng <= tải trọng tối đa của Drone.
    """
    clean_ids = list(dict.fromkeys([str(oid).strip() for oid in order_ids if str(oid).strip()]))
    if not clean_ids:
        raise HTTPException(status_code=400, detail="Bundled order list cannot be empty")

    active_order_ids = await get_active_mission_order_ids(db)
    orders: list[Order] = []
    total_payload_kg = 0.0
    total_fee = 0.0

    ref_origin = None
    ref_dest = None

    orders_res = await db.execute(select(Order).where(Order.id.in_(clean_ids)))
    orders_by_id = {o.id: o for o in orders_res.scalars().all()}

    for oid in clean_ids:
        o = orders_by_id.get(oid)
        if not o:
            raise HTTPException(status_code=404, detail=f"Order #{oid} not found")

        if strict_payment and o.payment_status != PaymentStatus.PAID:
            raise HTTPException(status_code=400, detail=f"Order #{oid} has not been paid (PAID)")

        if o.status != OrderStatus.AT_HUB:
            raise HTTPException(status_code=400, detail=f"Order #{oid} is not yet ready at the hub (AT_HUB)")

        if o.id in active_order_ids:
            raise HTTPException(status_code=400, detail=f"Order #{oid} already has an active flight mission")

        if ref_origin is None:
            ref_origin = o.origin_hub_id
            ref_dest = o.destination_hub_id
        else:
            if o.origin_hub_id != ref_origin or o.destination_hub_id != ref_dest:
                raise HTTPException(
                    status_code=400,
                    detail=f"Order #{oid} route (Hub #{o.origin_hub_id} -> #{o.destination_hub_id}) does not match the other orders in the bundle (Hub #{ref_origin} -> #{ref_dest})"
                )

        payload = float(o.payload_kg or 0.0)
        fee = float(o.delivery_fee or 0.0)
        total_payload_kg += payload
        total_fee += fee
        orders.append(o)

    total_payload_kg = round(total_payload_kg, 2)
    total_fee = round(total_fee, 2)

    drone_obj = None
    drone_capacity_kg = None
    remaining_capacity_kg = None
    is_valid = True
    validation_message = "Order bundle is valid for flight consolidation."

    if drone_id is not None:
        clean_drone_id = str(drone_id).strip()
        drone_obj = await db.get(Drone, clean_drone_id)
        if not drone_obj and clean_drone_id.isdigit():
            num_suffix = f"{int(clean_drone_id):04d}"
            alt_id = f"DRN-{num_suffix}"
            drone_obj = await db.get(Drone, alt_id)
            if not drone_obj:
                d_res = await db.execute(
                    select(Drone).where(Drone.id.like(f"%-DRN{num_suffix}")).limit(1)
                )
                drone_obj = d_res.scalar_one_or_none()

        if not drone_obj:
            raise HTTPException(status_code=404, detail=f"Drone '{drone_id}' not found")

        drone_capacity_kg = float(drone_obj.payload_capacity_kg or 0.0)
        remaining_capacity_kg = round(drone_capacity_kg - total_payload_kg, 2)

        if total_payload_kg > drone_capacity_kg:
            is_valid = False
            validation_message = (
                f"Total payload ({total_payload_kg} kg) exceeds the capacity of Drone "
                f"#{drone_obj.id} ({drone_obj.name}: {drone_capacity_kg} kg). Please select a larger drone or remove some orders."
            )

    return {
        "is_valid": is_valid,
        "message": validation_message,
        "total_orders": len(orders),
        "order_ids": clean_ids,
        "total_payload_kg": total_payload_kg,
        "total_delivery_fee": total_fee,
        "origin_hub_id": ref_origin,
        "destination_hub_id": ref_dest,
        "drone_id": drone_obj.id if drone_obj else None,
        "drone_name": drone_obj.name if drone_obj else None,
        "drone_capacity_kg": drone_capacity_kg,
        "remaining_capacity_kg": remaining_capacity_kg,
        "orders_detail": [
            {
                "order_id": o.id,
                "package_label": o.package_label,
                "payload_kg": float(o.payload_kg or 0.0),
                "receiver_name": o.receiver_name,
                "delivery_fee": float(o.delivery_fee or 0.0),
                "payment_status": o.payment_status.value if o.payment_status else "PENDING",
                "is_paid": o.payment_status == PaymentStatus.PAID,
            }
            for o in orders
        ]
    }

# ══════════════════════════════════════════════════════════════════════════════
# LUỒNG 2: POST-ANALYZE CONSOLIDATION (Ghép đơn sau khi đã Phân tích Tuyến đường)
# ══════════════════════════════════════════════════════════════════════════════

async def get_post_analyze_bundle_candidates(
    db: AsyncSession,
    primary_order_id: str,
    drone_id: Union[str, int],
    route_id: Optional[str] = None,
    require_paid: bool = False,
) -> dict[str, Any]:
    """
    Hiển thị các đơn hàng có thể ghép thêm vào Drone và tuyến đường đã chọn:
    - Tính tải trọng còn dư của Drone sau khi chở đơn chính:
        remaining_capacity = drone.payload_capacity - primary_order.payload
    - Tìm các đơn hàng cùng Hub xuất phát & Hub đích, có payload <= remaining_capacity.
    - Sắp xếp theo FIFO (đơn tạo trước được ưu tiên ghép).
    - Không yêu cầu phân tích lại lộ trình (tránh vòng lặp lớn).
    """
    clean_order_id = str(primary_order_id).strip()
    primary_order = await db.get(Order, clean_order_id)
    if not primary_order:
        raise HTTPException(status_code=404, detail=f"Order '{primary_order_id}' not found")

    clean_drone_id = str(drone_id).strip()
    drone_obj = await db.get(Drone, clean_drone_id)
    if not drone_obj and clean_drone_id.isdigit():
        num_suffix = f"{int(clean_drone_id):04d}"
        alt_id = f"DRN-{num_suffix}"
        drone_obj = await db.get(Drone, alt_id)
        if not drone_obj:
            d_res = await db.execute(
                select(Drone).where(Drone.id.like(f"%-DRN{num_suffix}")).limit(1)
            )
            drone_obj = d_res.scalar_one_or_none()

    if not drone_obj:
        raise HTTPException(status_code=404, detail=f"Drone '{drone_id}' not found")

    primary_payload = float(primary_order.payload_kg or 0.0)
    drone_capacity = float(drone_obj.payload_capacity_kg or 0.0)
    remaining_capacity = round(drone_capacity - primary_payload, 2)

    hub_map = {h.id: h for h in await get_all_hubs_cached(db)}
    origin_hub = hub_map.get(primary_order.origin_hub_id)
    dest_hub = hub_map.get(primary_order.destination_hub_id)

    candidates = await get_available_orders_query(
        db=db,
        origin_hub_id=primary_order.origin_hub_id,
        destination_hub_id=primary_order.destination_hub_id,
        exclude_order_ids={primary_order.id},
        require_paid=require_paid,
    )

    eligible_candidates = []
    for o in candidates:
        order_weight = float(o.payload_kg or 0.0)
        can_fit = order_weight <= remaining_capacity
        eligible_candidates.append({
            "order_id": o.id,
            "package_label": o.package_label,
            "payload_kg": order_weight,
            "receiver_name": o.receiver_name,
            "receiver_phone": o.receiver_phone,
            "delivery_fee": float(o.delivery_fee or 0.0),
            "payment_status": o.payment_status.value if o.payment_status else "PENDING",
            "is_paid": o.payment_status == PaymentStatus.PAID,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "can_fit_in_remaining_capacity": can_fit,
            "remaining_after_this_order": round(remaining_capacity - order_weight, 2) if can_fit else None,
        })

    # Đưa các đơn vừa tải lên đầu
    eligible_candidates.sort(key=lambda x: (not x["can_fit_in_remaining_capacity"], x["created_at"] or ""))

    return {
        "workflow": "POST_ANALYZE",
        "route_id": route_id,
        "primary_order": {
            "order_id": primary_order.id,
            "package_label": primary_order.package_label,
            "payload_kg": primary_payload,
            "origin_hub_id": primary_order.origin_hub_id,
            "origin_hub_name": origin_hub.name if origin_hub else None,
            "destination_hub_id": primary_order.destination_hub_id,
            "destination_hub_name": dest_hub.name if dest_hub else None,
            "payment_status": primary_order.payment_status.value if primary_order.payment_status else "PENDING",
            "is_paid": primary_order.payment_status == PaymentStatus.PAID,
        },
        "drone": {
            "id": drone_obj.id,
            "name": drone_obj.name,
            "model": drone_obj.model,
            "payload_capacity_kg": drone_capacity,
        },
        "capacity_summary": {
            "drone_capacity_kg": drone_capacity,
            "primary_order_payload_kg": primary_payload,
            "remaining_capacity_kg": remaining_capacity,
            "is_full": remaining_capacity <= 0,
        },
        "available_bundle_candidates": eligible_candidates,
        "total_available_candidates": len(eligible_candidates),
        "fittable_candidates_count": sum(1 for c in eligible_candidates if c["can_fit_in_remaining_capacity"]),
    }
