"""
app/modules/missions/auto_dispatch_service.py
─────────────────────────────────────────────
Dịch vụ Tự động Điều phối Nhiệm vụ bay (Auto-Dispatch) thay thế toàn bộ
bước chọn Drone, chọn Pin, chọn Route và tạo Mission thủ công của Operator,
đồng thời giữ lại hệ thống giải thích XAI SHAP minh bạch (tránh black-box).

Quy ước ngôn ngữ:
- Comment & docstring nội bộ: Tiếng Việt.
- Toàn bộ thông điệp trả về FE (HTTPException, SHAP labels/impacts/counterfactuals, summary): Tiếng Anh kỹ thuật tự nhiên.
"""

import copy
import inspect
from typing import Any, Awaitable, Callable, Optional
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai_predictions.service import haversine_distance
from app.modules.fleet.models import (
    Battery,
    Drone,
    DroneStatus,
    batteries_status,
    compute_battery_lifecycle,
)
from app.modules.missions.bundle_service import (
    ACTIVE_MISSION_STATUSES,
    get_all_hubs_cached,
    get_compatible_battery_models_map,
    resolve_compatible_models_from_map,
    resolve_effective_origin_from_missions,
    resolve_effective_origin_hub_id,
    validate_bundle_orders,
)
from app.modules.missions.models import (
    DeliveryMode,
    Mission,
    MissionStatus,
    Order,
    OrderStatus,
    PaymentStatus,
)
from app.modules.missions.schemas import (
    AlternativeComparisonItem,
    AutoDispatchXAIReport,
    CandidateBatteryOption,
    CandidateDroneOption,
    DecisionShapExplanation,
    NaturalLanguageShapExplanation,
    RecommendResourcesResponse,
    RouteBusinessRuleCheck,
    DroneBusinessRuleSummary,
    RouteOptionSchema,
    ShapFeatureAttribution,
)
from app.modules.missions.technician.router import get_compatible_battery_models
from app.modules.system.models import Hub


def _compute_additive_shap(
    candidates: list[dict[str, Any]],
    selected_idx: int,
    feature_specs: list[tuple[str, str, float]],
    why_selected: str,
    counterfactual_builder: Callable[[dict[str, Any], float], dict[str, Any]],
    max_counterfactuals: int = 3,
) -> DecisionShapExplanation:
    """
    Tính giá trị Shapley cộng tính chính xác (Additive SHAP):
    - base_value + sum(shap_values) == total_score (đảm bảo tiên đề Efficiency).
    - Nếu có >= 2 ứng viên: dùng Pool Average làm baseline đối chứng.
    - Nếu chỉ có 1 ứng viên: dùng ngưỡng trung bình tham chiếu 0.50 (50.0 điểm) để biểu diễn đóng góp từng tiêu chí.
    """
    selected = candidates[selected_idx]
    scores = [
        round(sum(w * float(c["utilities"].get(k, 0.0)) for k, _, w in feature_specs), 2)
        for c in candidates
    ]
    total_score = scores[selected_idx]

    pool_size = len(candidates)
    if pool_size >= 2:
        expected_u = {
            k: sum(float(c["utilities"].get(k, 0.0)) for c in candidates) / pool_size
            for k, _, _ in feature_specs
        }
        baseline_ref = f"Pool Average ({pool_size} candidates evaluated)"
    else:
        expected_u = {k: 0.5 for k, _, _ in feature_specs}
        baseline_ref = "Neutral Reference Baseline (single feasible candidate, baseline = 50.00 pts)"

    base_value = round(sum(w * expected_u[k] for k, _, w in feature_specs), 2)

    raw_phis = [
        w * (float(selected["utilities"].get(k, 0.0)) - expected_u[k])
        for k, _, w in feature_specs
    ]
    rounded_phis = [round(p, 2) for p in raw_phis]

    # Điều chỉnh sai số làm tròn <= 0.02 ở tiêu chí cuối để giữ bất biến base_value + sum == total_score
    drift = round(total_score - (base_value + sum(rounded_phis)), 2)
    if rounded_phis and drift != 0.0:
        rounded_phis[-1] = round(rounded_phis[-1] + drift, 2)

    shap_values: dict[str, float] = {}
    feature_contributions: list[ShapFeatureAttribution] = []
    for (key, label, _), phi in zip(feature_specs, rounded_phis):
        shap_values[key] = phi
        direction = "positive" if phi > 0.05 else ("negative" if phi < -0.05 else "neutral")
        feature_contributions.append(
            ShapFeatureAttribution(
                feature=key,
                label=label,
                raw_value=str(selected["raw_values"].get(key, "N/A")),
                shap_contribution=phi,
                direction=direction,
                impact=str(selected["impacts"].get(key, "")),
            )
        )

    other_pairs = [
        (c, sc)
        for idx, (c, sc) in enumerate(zip(candidates, scores))
        if idx != selected_idx
    ]
    other_pairs.sort(key=lambda item: -item[1])
    counterfactuals = [
        counterfactual_builder(cand, round(cand_score - total_score, 2))
        for cand, cand_score in other_pairs[:max_counterfactuals]
    ]

    return DecisionShapExplanation(
        selected_id=str(selected["id"]),
        selected_label=str(selected["label"]),
        total_score=total_score,
        base_value=base_value,
        baseline_reference=baseline_ref,
        shap_values=shap_values,
        feature_contributions=feature_contributions,
        why_selected=why_selected,
        counterfactuals=counterfactuals,
    )


async def _validate_order_and_bundle_for_auto_dispatch(
    db: AsyncSession,
    order_id: str,
    bundled_order_ids: Optional[list[str]],
    order_missions_out: Optional[list[Mission]] = None,
) -> tuple[Order, list[str], float, float]:
    """
    Kiểm tra điều kiện hợp lệ của đơn hàng chính và các đơn hàng gộp (nếu có).
    [PERF NOTE] Tải Mission của đơn hàng trong 1 query duy nhất để vừa check trùng/sự cố vừa tính eff_origin_hub_id.
    """
    clean_order_id = order_id.strip() if order_id else ""
    order = await db.get(Order, clean_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order has not been paid")

    if order.status != OrderStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order is currently in '{order.status.value}' status. "
                "Auto-dispatch can only create a mission once a Technician confirms package receipt at the hub (AT_HUB)."
            ),
        )

    order_missions = list(
        (
            await db.execute(
                select(Mission)
                .where(Mission.order_id == order.id)
                .order_by(Mission.id.desc())
            )
        ).scalars().all()
    )
    if order_missions_out is not None:
        order_missions_out.extend(order_missions)

    if any(m.status in ACTIVE_MISSION_STATUSES for m in order_missions):
        raise HTTPException(
            status_code=400,
            detail="This order already has a scheduled or active mission",
        )

    if any(
        m.status in (MissionStatus.INCIDENT_RETURN, MissionStatus.FAILED)
        and (m.arrival_confirmed_at is None or m.handling_status is not None)
        for m in order_missions
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "A previous flight for this order encountered an incident or disconnection, and a Technician "
                "has not yet confirmed drone/package recovery at a hub. Please wait for recovery confirmation before auto-dispatching."
            ),
        )

    clean_bundle_ids = list(
        dict.fromkeys(
            str(oid).strip()
            for oid in (bundled_order_ids or [])
            if str(oid).strip() and str(oid).strip() != clean_order_id
        )
    )
    total_payload_kg = float(order.payload_kg or 0.0)
    total_fee = float(order.delivery_fee or 0.0)

    if clean_bundle_ids:
        val_res = await validate_bundle_orders(db, [clean_order_id] + clean_bundle_ids, drone_id=None)
        if not val_res["is_valid"]:
            raise HTTPException(status_code=400, detail=val_res["message"])
        total_payload_kg = float(val_res["total_payload_kg"])
        total_fee = float(val_res["total_delivery_fee"])

    return order, clean_bundle_ids, round(total_payload_kg, 2), round(total_fee, 2)


def _compute_battery_wh_local(battery: Battery, fallback_cap_wh: float = 130.0) -> tuple[float, int, float]:
    """Tính toán (capacity_wh, charge_level_pct, available_energy_wh) thực tế từ DB."""
    cap = float(battery.capacity_wh if battery.capacity_wh is not None else fallback_cap_wh)
    pct = int(battery.charge_level_pct if battery.charge_level_pct is not None else 0)
    return cap, pct, round(cap * (pct / 100.0), 2)


def _evaluate_battery_candidates_list(
    compatible_bats: list[Battery],
    drone: Drone,
    req_leg1_wh: float,
) -> tuple[list[dict[str, Any]], int, DecisionShapExplanation]:
    """Đánh giá toàn bộ pin tương thích tại Hub xuất phát của Drone và sinh báo cáo SHAP."""
    feature_specs = [
        ("energy_reserve_margin", "Energy Safety Margin (20% Buffer)", 40.0),
        ("state_of_charge", "Current State of Charge (%)", 25.0),
        ("mounting_efficiency", "Airframe Pre-Mount Readiness", 20.0),
        ("battery_lifecycle_health", "Battery Lifecycle Health", 15.0),
    ]

    bat_candidates: list[dict[str, Any]] = []
    for bat in compatible_bats:
        cap_wh, charge_pct, avail_wh = _compute_battery_wh_local(bat)
        safe_usable_wh = avail_wh * 0.80
        meets_reserve = safe_usable_wh >= req_leg1_wh and charge_pct > 0
        is_installed = bat.drone_id == drone.id
        lifecycle_status, days_left = compute_battery_lifecycle(bat)
        is_expired = str(getattr(lifecycle_status, "value", lifecycle_status)) == "Expired"

        if meets_reserve:
            headroom_ratio = (avail_wh - req_leg1_wh) / max(avail_wh, 1.0)
            u_margin = min(1.0, 0.55 + 0.45 * headroom_ratio)
            margin_pct = round(headroom_ratio * 100.0, 1)
            margin_impact = (
                f"Provides {avail_wh:.1f} Wh usable energy ({safe_usable_wh:.1f} Wh within 80% safe cap) vs "
                f"{req_leg1_wh:.1f} Wh Leg 1 demand ({margin_pct:.1f}% total reserve)"
            )
        else:
            deficit_ratio = avail_wh / max(req_leg1_wh / 0.80, 1.0)
            u_margin = max(0.0, min(0.45, 0.45 * deficit_ratio))
            margin_impact = (
                f"Insufficient 20% safety buffer: 80% safe energy ({safe_usable_wh:.1f} Wh) < {req_leg1_wh:.1f} Wh required"
            )

        u_soc = max(0.0, min(1.0, charge_pct / 100.0))
        soc_impact = (
            "Fully charged (100% SoC) and immediately cleared for takeoff"
            if charge_pct >= 100
            else f"Charged at {charge_pct}% SoC"
        )

        u_mount = 1.0 if is_installed else 0.45
        mount_raw = f"Installed on {drone.id}" if is_installed else f"Rack spare at Hub #{bat.current_hub_id}"
        mount_impact = (
            f"Already pre-mounted on {drone.name} — eliminates manual battery swap before takeoff"
            if is_installed
            else "Available in hub storage rack — requires technician mounting before takeoff"
        )

        u_health = 0.0 if is_expired else round(0.5 + 0.5 * min(1.0, max(0, days_left) / 730.0), 4)
        health_raw = "Expired" if is_expired else f"Active ({days_left} days remaining)"
        health_impact = (
            "Battery exceeded lifecycle warranty threshold"
            if is_expired
            else f"Healthy lifecycle standing with {days_left} operational days remaining"
        )

        # Ưu tiên tuyệt đối cho pin đạt biên dự phòng >=20% và chưa hết hạn
        hard_priority = (
            1 if meets_reserve else 0,
            1 if charge_pct >= 100 else 0,
            1 if not is_expired else 0,
        )
        weighted_score = (
            40.0 * u_margin + 25.0 * u_soc + 20.0 * u_mount + 15.0 * u_health
        )

        bat_candidates.append({
            "battery": bat,
            "id": bat.id,
            "label": f"{bat.id} ({bat.serial_number})",
            "meets_reserve": meets_reserve,
            "is_expired": is_expired,
            "is_installed": is_installed,
            "days_left": days_left,
            "cap_wh": cap_wh,
            "hard_priority": hard_priority,
            "weighted_score": round(weighted_score, 2),
            "utilities": {
                "energy_reserve_margin": u_margin,
                "state_of_charge": u_soc,
                "mounting_efficiency": u_mount,
                "battery_lifecycle_health": u_health,
            },
            "raw_values": {
                "energy_reserve_margin": f"{avail_wh:.1f} / {cap_wh:.0f} Wh (need <= {safe_usable_wh:.1f} Wh safe)",
                "state_of_charge": f"{charge_pct}%",
                "mounting_efficiency": mount_raw,
                "battery_lifecycle_health": health_raw,
            },
            "impacts": {
                "energy_reserve_margin": margin_impact,
                "state_of_charge": soc_impact,
                "mounting_efficiency": mount_impact,
                "battery_lifecycle_health": health_impact,
            },
            "avail_wh": avail_wh,
            "charge_pct": charge_pct,
        })

    best_idx = max(
        range(len(bat_candidates)),
        key=lambda i: (bat_candidates[i]["hard_priority"], bat_candidates[i]["weighted_score"], -i),
    )
    chosen = bat_candidates[best_idx]
    chosen_bat: Battery = chosen["battery"]

    why_battery = (
        f"Selected battery {chosen_bat.id} ({chosen_bat.serial_number}) at {chosen['charge_pct']}% SoC "
        f"({chosen['avail_wh']:.1f} Wh usable vs {req_leg1_wh:.1f} Wh Leg-1 demand), "
        + ("already pre-mounted on the assigned drone " if chosen_bat.drone_id == drone.id else "ready in the departure hub rack ")
        + f"with total utility score {chosen['weighted_score']:.2f}/100."
    )

    bat_shap = _compute_additive_shap(
        candidates=bat_candidates,
        selected_idx=best_idx,
        feature_specs=feature_specs,
        why_selected=why_battery,
        counterfactual_builder=lambda c, delta: {
            "candidate_id": c["id"],
            "candidate_label": c["label"],
            "score": c["weighted_score"],
            "score_delta_vs_selected": delta,
            "meets_safety_reserve": c["meets_reserve"],
            "comparison_note": (
                f"Scored {abs(delta):.2f} pts lower "
                f"({c['raw_values']['state_of_charge']} SoC, {c['raw_values']['mounting_efficiency']})."
            ),
        },
    )
    return bat_candidates, best_idx, bat_shap


def _score_battery_candidates_and_explain(
    compatible_bats: list[Battery],
    drone: Drone,
    req_leg1_wh: float,
) -> tuple[Battery, DecisionShapExplanation]:
    """Chấm điểm toàn bộ pin tương thích tại Hub xuất phát của Drone và sinh báo cáo SHAP."""
    bat_candidates, best_idx, bat_shap = _evaluate_battery_candidates_list(compatible_bats, drone, req_leg1_wh)
    return bat_candidates[best_idx]["battery"], bat_shap


def _score_and_explain_routes(
    routes_schemas: list[RouteOptionSchema],
    selected_route: RouteOptionSchema,
    optimization_objective: str,
) -> DecisionShapExplanation:
    """Giải thích XAI SHAP cho quyết định lựa chọn tuyến bay tối ưu so với các phương án còn lại."""
    feature_specs = [
        ("safety_and_weather_margin", "Weather Safety & Reserve Margin", 35.0),
        ("energy_efficiency", "Predicted Energy Efficiency", 25.0),
        ("sla_time_alignment", f"Delivery Mode Fit ({optimization_objective})", 25.0),
        ("operational_simplicity", "Direct vs Relay Simplicity", 15.0),
    ]

    feasible_routes = [r for r in routes_schemas if r.feasible]
    min_energy = min((r.predictedEnergyWh for r in feasible_routes if r.predictedEnergyWh > 0), default=1.0)
    min_duration = min((r.predictedDurationMin for r in feasible_routes if r.predictedDurationMin > 0), default=1)

    route_candidates: list[dict[str, Any]] = []
    selected_idx = 0
    for idx, r in enumerate(routes_schemas):
        if r.routeId == selected_route.routeId:
            selected_idx = idx

        is_bad_w = bool(r.weather and r.weather.is_bad_weather)
        num_legs = len(r.legs_detail) if r.legs_detail else 1
        speed_mps = float(r.flight_speed_mps or 10.0)

        if not r.feasible:
            u_safety = 0.0
            safety_impact = f"Disqualified: {r.infeasible_reason or 'Insufficient energy reserve (<20%)'}"
        elif is_bad_w:
            u_safety = 0.25
            w_warn = (r.weather.warning_message if r.weather else None) or "adverse weather conditions"
            safety_impact = f"Weather penalty applied: {w_warn}"
        else:
            u_safety = min(1.0, 0.65 + 0.35 * max(0.0, float(r.remainingBatteryPct or 0) / 100.0))
            safety_impact = (
                f"Cleared weather thresholds with {r.remainingBatteryPct}% remaining battery reserve"
            )

        u_energy = (
            min(1.0, max(0.15, min_energy / max(float(r.predictedEnergyWh or 1.0), 0.1)))
            if r.feasible else 0.1
        )
        energy_impact = f"Estimated consumption {r.predictedEnergyWh:.1f} Wh over {r.distanceKm:.2f} km"

        if optimization_objective == "FAST":
            u_sla = min(1.0, max(0.2, min_duration / max(int(r.predictedDurationMin or 1), 1)))
            sla_impact = f"Speed {speed_mps:.0f} m/s completes trip in {r.predictedDurationMin} min (FAST objective)"
        else:
            if "SAFE" in r.routeId or speed_mps < 9.0:
                u_sla = 0.70
                sla_impact = (
                    f"Conservative low-speed profile ({speed_mps:.0f} m/s) extends transit time "
                    f"to {r.predictedDurationMin} min vs optimal Eco cruise (10 m/s)"
                )
            elif speed_mps <= 10.5:
                u_sla = 1.0
                sla_impact = f"Cruise speed {speed_mps:.0f} m/s avoids v^2 aerodynamic drag penalty (ECO objective)"
            else:
                u_sla = 0.5
                sla_impact = f"High-speed {speed_mps:.0f} m/s profile incurs aerodynamic drag penalty under ECO mode"

        if num_legs <= 1 and not r.is_cross_hub:
            u_simple = 1.0
            simple_raw = "Direct flight (1 leg)"
            simple_impact = "Non-stop direct corridor avoids intermediate landing and battery swap overhead"
        elif num_legs == 2 or r.is_cross_hub:
            u_simple = 0.65
            simple_raw = f"{num_legs} legs" + (" (Cross-Hub)" if r.is_cross_hub else " (1 Relay Stop)")
            simple_impact = "Includes intermediate positioning or hub battery swap step"
        else:
            u_simple = 0.45
            simple_raw = f"{num_legs} legs (Multi-Relay)"
            simple_impact = "Multi-stop relay trajectory adds turnaround latency"

        w_summary = r.weather_summary or ("Weather alert" if is_bad_w else "Safe weather")
        route_candidates.append({
            "route": r,
            "id": r.routeId,
            "label": f"{r.routeId}: {r.route_string}",
            "utilities": {
                "safety_and_weather_margin": u_safety,
                "energy_efficiency": u_energy,
                "sla_time_alignment": u_sla,
                "operational_simplicity": u_simple,
            },
            "raw_values": {
                "safety_and_weather_margin": f"Feasible={r.feasible}, Reserve={r.remainingBatteryPct}%, {w_summary}",
                "energy_efficiency": f"{r.predictedEnergyWh:.1f} Wh ({r.distanceKm:.2f} km)",
                "sla_time_alignment": f"{r.predictedDurationMin} min @ {speed_mps:.0f} m/s ({int(r.flight_altitude_m or 45)}m)",
                "operational_simplicity": simple_raw,
            },
            "impacts": {
                "safety_and_weather_margin": safety_impact,
                "energy_efficiency": energy_impact,
                "sla_time_alignment": sla_impact,
                "operational_simplicity": simple_impact,
            },
        })

    why_route = selected_route.reason or (
        f"Selected feasible route {selected_route.routeId} ({selected_route.route_string}) "
        f"aligned with {optimization_objective} priority."
    )

    return _compute_additive_shap(
        candidates=route_candidates,
        selected_idx=selected_idx,
        feature_specs=feature_specs,
        why_selected=why_route,
        counterfactual_builder=lambda c, delta: {
            "candidate_id": c["id"],
            "candidate_label": c["label"],
            "score": round(
                35.0 * c["utilities"]["safety_and_weather_margin"]
                + 25.0 * c["utilities"]["energy_efficiency"]
                + 25.0 * c["utilities"]["sla_time_alignment"]
                + 15.0 * c["utilities"]["operational_simplicity"],
                2,
            ),
            "score_delta_vs_selected": delta,
            "feasible": c["route"].feasible,
            "comparison_note": (
                c["route"].infeasible_reason
                if not c["route"].feasible and c["route"].infeasible_reason
                else (
                    f"Estimated {c['route'].predictedEnergyWh:.1f} Wh, {c['route'].predictedDurationMin} min "
                    f"(reserve {c['route'].remainingBatteryPct}%)."
                )
            ),
        },
    )


def _extract_top_shap_reasons(shap_expl: DecisionShapExplanation) -> list[str]:
    """Trích xuất lý do bằng tiếng Anh tự nhiên từ SHAP attributions theo thứ tự tác động mạnh nhất."""
    sorted_contribs = sorted(
        shap_expl.feature_contributions,
        key=lambda fc: fc.shap_contribution,
        reverse=True,
    )
    reasons = [
        fc.impact
        for fc in sorted_contribs
        if fc.impact and not fc.impact.startswith("Disqualified")
    ]
    return reasons or [fc.impact for fc in sorted_contribs if fc.impact]


def _compare_drone_vs_winner(
    alt: dict[str, Any],
    winner: dict[str, Any],
    total_payload_kg: float,
) -> str:
    """Sinh câu giải thích bằng tiếng Anh lý do vì sao không chọn Drone thay thế này."""
    if not alt["is_feasible"]:
        return f"Disqualified: {alt.get('infeasible_reason') or 'No compatible battery or feasible route'}"

    alt_d: Drone = alt["drone"]
    win_d: Drone = winner["drone"]
    if not alt["is_local"] and winner["is_local"]:
        return (
            f"Not selected: Stationed at {alt['raw_values']['hub_locality']}, "
            f"requiring an empty positioning flight compared to {win_d.id} at the departure hub."
        )
    if winner["utilities"]["payload_fit"] > alt["utilities"]["payload_fit"] + 0.05:
        return (
            f"Not selected: {float(alt_d.payload_capacity_kg or 0):.1f} kg airframe is oversized "
            f"for the {total_payload_kg:.2f} kg parcel compared to {win_d.id} ({float(win_d.payload_capacity_kg or 0):.1f} kg), "
            f"causing higher aerodynamic drag and energy waste."
        )
    if winner["utilities"]["energy_readiness"] > alt["utilities"]["energy_readiness"] + 0.03:
        alt_res = alt["rec_route"].remainingBatteryPct if alt.get("rec_route") else 0
        win_res = winner["rec_route"].remainingBatteryPct if winner.get("rec_route") else 0
        return (
            f"Not selected: Leaves a lower post-flight battery safety reserve ({alt_res}%) "
            f"compared to {win_d.id} ({win_res}%)."
        )
    alt_util = float(alt_d.utilization_pct or 0)
    win_util = float(win_d.utilization_pct or 0)
    if alt_util > win_util + 0.1:
        return (
            f"Not selected: Higher cumulative fleet utilization ({alt_util:.1f}% vs "
            f"{win_util:.1f}% on {win_d.id}), kept idle to balance fleet wear."
        )
    return (
        f"Not selected: Equivalent capabilities to {win_d.id} ({float(alt_d.payload_capacity_kg or 0):.1f} kg capacity, "
        f"{alt['raw_values']['hub_locality']}); kept available as an alternative option."
    )


def _compare_battery_vs_winner(
    alt: dict[str, Any],
    winner: dict[str, Any],
    drone: Drone,
    req_leg1_wh: float,
) -> str:
    """Sinh câu giải thích bằng tiếng Anh lý do vì sao không chọn viên Pin thay thế này."""
    if not alt["meets_reserve"]:
        safe_wh = alt["avail_wh"] * 0.80
        return (
            f"Disqualified: Usable safe energy ({safe_wh:.1f} Wh within 80% cap) cannot cover "
            f"Leg 1 demand ({req_leg1_wh:.1f} Wh) with the mandatory 20% safety reserve."
        )
    if alt.get("is_expired"):
        return "Disqualified: Battery has exceeded its safe operational lifecycle warranty."

    win_bat: Battery = winner["battery"]
    if alt["charge_pct"] < winner["charge_pct"]:
        return (
            f"Not selected: Lower state of charge ({alt['charge_pct']}% vs {winner['charge_pct']}% on {win_bat.id}), "
            f"providing a smaller emergency energy buffer."
        )
    if not alt.get("is_installed") and winner.get("is_installed"):
        return (
            f"Not selected: Stored in the hub rack (requires manual Technician swap), "
            f"whereas {win_bat.id} is already pre-mounted on Drone {drone.id}."
        )
    if alt["avail_wh"] < winner["avail_wh"]:
        return (
            f"Not selected: Lower total available energy ({alt['avail_wh']:.1f} Wh vs "
            f"{winner['avail_wh']:.1f} Wh on {win_bat.id})."
        )
    if alt.get("days_left", 0) < winner.get("days_left", 0):
        return (
            f"Not selected: Older lifecycle standing ({alt['raw_values']['battery_lifecycle_health']}) "
            f"compared to {win_bat.id} ({winner['raw_values']['battery_lifecycle_health']})."
        )
    return (
        f"Not selected: Equivalent charge and health specifications to {win_bat.id} "
        f"({alt['charge_pct']}% SoC, {alt['avail_wh']:.1f} Wh); kept in reserve as an equally valid option."
    )


def build_route_nl_explanation(
    routes_schemas: list[RouteOptionSchema],
    selected_route: RouteOptionSchema,
    optimization_objective: str,
) -> NaturalLanguageShapExplanation:
    """Tạo giải thích XAI SHAP hoàn toàn bằng tiếng Anh cho quyết định chọn Tuyến đường (Bước 2)."""
    route_shap = _score_and_explain_routes(
        routes_schemas=routes_schemas,
        selected_route=selected_route,
        optimization_objective=optimization_objective,
    )
    reasons_selected = _extract_top_shap_reasons(route_shap)

    if not selected_route.feasible:
        summary = (
            f"No feasible route found under current battery/weather constraints: "
            f"{selected_route.infeasible_reason or 'all candidate routes exceed safe 80% battery reserve or weather limits.'}"
        )
    else:
        summary = (
            selected_route.reason
            if selected_route.reason and not selected_route.reason.startswith("Infeasible")
            else (
                f"Selected route {selected_route.routeId} ({selected_route.route_string}) under {optimization_objective} priority: "
                f"{selected_route.predictedDurationMin} min flight time, {selected_route.predictedEnergyWh:.1f} Wh estimated energy, "
                f"and {selected_route.remainingBatteryPct}% landing battery reserve."
            )
        )

    why_not_others: list[AlternativeComparisonItem] = []
    for r in routes_schemas:
        if r.routeId == selected_route.routeId:
            continue
        label = f"{r.routeId} ({r.route_string})"
        if not r.feasible:
            reason = f"Disqualified: {r.infeasible_reason or 'Insufficient energy reserve or weather violation'}"
        elif not r.is_selectable:
            reason = f"Not selectable: {r.unselectable_reason or 'Route constraints not satisfied'}"
        else:
            is_r_bad_w = (
                bool(r.weather and r.weather.is_bad_weather)
                or (bool(r.weather_summary) and ("alert" in r.weather_summary.lower() or "unsafe" in r.weather_summary.lower()))
            )
            if is_r_bad_w:
                w_detail = (r.weather.warning_message if r.weather and r.weather.warning_message else None) or r.weather_summary or "wind/rain alert"
                reason = (
                    f"Not selected: Exposed to adverse weather conditions ({w_detail}), "
                    f"whereas {selected_route.routeId} avoids this risk."
                )
            elif optimization_objective == "ECO" and r.predictedEnergyWh > selected_route.predictedEnergyWh + 0.5:
                reason = (
                    f"Not selected: Consumes more battery energy ({r.predictedEnergyWh:.1f} Wh vs "
                    f"{selected_route.predictedEnergyWh:.1f} Wh on {selected_route.routeId}) due to higher cruise speed or longer detour ({r.distanceKm:.2f} km)."
                )
            elif optimization_objective == "FAST" and r.predictedDurationMin > selected_route.predictedDurationMin:
                reason = (
                    f"Not selected: Slower transit duration ({r.predictedDurationMin} min vs "
                    f"{selected_route.predictedDurationMin} min on {selected_route.routeId}), less aligned with Express delivery SLA."
                )
            elif "DETOUR" in r.routeId and "DETOUR" not in selected_route.routeId:
                reason = (
                    f"Not selected: Alternate bypass corridor ({r.distanceKm:.2f} km, {r.predictedEnergyWh:.1f} Wh, {r.predictedDurationMin} min) "
                    f"is held in reserve and only recommended when primary routes violate battery reserve (80%) or weather safety limits."
                )
            elif "SAFE" in r.routeId and "SAFE" not in selected_route.routeId:
                spd_kmh = int(round(float(r.flight_speed_mps or 8.0) * 3.6))
                alt_m = int(round(float(r.flight_altitude_m or 40.0)))
                reason = (
                    f"Not selected: Conservative low-speed profile ({spd_kmh} km/h, Altitude {alt_m}m, "
                    f"{r.predictedDurationMin} min vs {selected_route.predictedDurationMin} min on {selected_route.routeId}) "
                    f"is prioritized mainly when primary routes breach tight battery limits or severe wind thresholds."
                )
            elif len(r.legs_detail) > len(selected_route.legs_detail):
                baseline_desc = (
                    "a direct flight"
                    if len(selected_route.legs_detail) <= 1
                    else f"{len(selected_route.legs_detail)} legs on {selected_route.routeId}"
                )
                reason = (
                    f"Not selected: Requires {len(r.legs_detail)} flight legs with intermediate relay battery swap, "
                    f"adding ground turnaround overhead compared to {baseline_desc}."
                )
            else:
                reason = (
                    f"Not selected: Less optimal trade-off between landing reserve ({r.remainingBatteryPct}%), "
                    f"energy consumption ({r.predictedEnergyWh:.1f} Wh), and duration ({r.predictedDurationMin} min)."
                )

        why_not_others.append(
            AlternativeComparisonItem(
                id=r.routeId,
                label=label,
                feasible=bool(r.feasible and r.is_selectable),
                reason=reason,
            )
        )

    return NaturalLanguageShapExplanation(
        selected_id=selected_route.routeId,
        selected_label=f"{selected_route.routeId}: {selected_route.route_string}",
        summary=summary,
        reasons_selected=reasons_selected,
        why_not_others=why_not_others,
    )


def build_route_business_rule_check(
    route: RouteOptionSchema,
    payload_ok: bool = True,
    is_available: bool = True,
    drone_status_label: str = "Available",
    order_payload_kg: float = 0.0,
    max_payload_kg: float = 0.0,
) -> RouteBusinessRuleCheck:
    """Tổng hợp kết quả kiểm tra Business Rule (tải trọng, pin + 20% safety buffer, relay & thời tiết) cho 1 tuyến."""
    strategy = (
        route.strategy_code
        or (route.routeId.rsplit("-", 1)[-1] if route.routeId else "ROUTE")
    )
    if strategy == "ENG":
        strategy = "ECO"

    legs = route.legs_detail or []
    is_multi_leg = len(legs) > 1
    leg1_energy = (
        float(legs[0].energy_wh or 0.0)
        if (is_multi_leg and legs[0].energy_wh is not None)
        else float(route.predictedEnergyWh or 0.0)
    )
    req_wh = round(leg1_energy, 2)
    req_with_buf_wh = round(req_wh / 0.80, 2) if req_wh > 0 else 0.0
    avail_wh = round(float(route.assignedBatteryEnergyWh or 0.0), 2)

    relay_ok = all(bool(st.feasible) for st in (route.relay_battery_status or []))
    weather_safe = not bool(route.weather and route.weather.is_bad_weather)
    meets_buf = bool(
        avail_wh > 0
        and req_wh <= (avail_wh * 0.80 + 0.05)
        and int(route.remainingBatteryPct or 0) >= 20
        and relay_ok
    )

    feasible = bool(route.feasible and payload_ok and is_available)
    infeas_reason = route.infeasible_reason
    if not is_available:
        infeas_reason = f"Drone status is '{drone_status_label}' (must be 'Available')."
    elif not payload_ok:
        infeas_reason = (
            f"Drone max payload ({max_payload_kg:.2f} kg) is below order payload ({order_payload_kg:.2f} kg)."
        )

    return RouteBusinessRuleCheck(
        route_id=route.routeId,
        strategy_code=strategy,
        display_title=route.display_title or route.speed_profile_label or route.routeId,
        route_string=route.route_string,
        required_energy_wh=req_wh,
        safety_buffer_pct=20,
        required_with_buffer_wh=req_with_buf_wh,
        available_energy_wh=avail_wh,
        battery_consumption_pct=int(route.batteryConsumptionPct or 0),
        remaining_battery_pct=int(route.remainingBatteryPct or 0),
        meets_20pct_buffer=meets_buf,
        relay_batteries_ok=relay_ok,
        weather_safe=weather_safe,
        feasible=feasible,
        infeasible_reason=infeas_reason,
    )


def build_drone_business_rule_summary(
    drone: Drone,
    order_payload_kg: float,
    compatible_battery_models: list[str],
    routes_schemas: list[RouteOptionSchema],
    assigned_battery: Optional[Battery] = None,
) -> DroneBusinessRuleSummary:
    """Tạo bảng Business Rule tổng hợp của Drone được chọn trên tất cả các tuyến bay."""
    d_status = drone.status.value if hasattr(drone.status, "value") else str(drone.status or "")
    is_avail = d_status == DroneStatus.AVAILABLE.value
    max_kg = float(drone.payload_capacity_kg or 0.0)
    payload_ok = max_kg >= float(order_payload_kg or 0.0)

    routes_br: list[RouteBusinessRuleCheck] = []
    for r in routes_schemas:
        br = build_route_business_rule_check(
            route=r,
            payload_ok=payload_ok,
            is_available=is_avail,
            drone_status_label=d_status,
            order_payload_kg=float(order_payload_kg or 0.0),
            max_payload_kg=max_kg,
        )
        r.business_rule_check = br
        routes_br.append(br)

    first_r = routes_schemas[0] if routes_schemas else None
    bat_id = (assigned_battery.id if assigned_battery else None) or (first_r.assignedBatteryId if first_r else None)
    bat_serial = (assigned_battery.serial_number if assigned_battery else None) or (first_r.assignedBatterySerial if first_r else None)
    bat_model = (assigned_battery.battery_model if assigned_battery else None) or (first_r.assignedBatteryModel if first_r else None)
    bat_energy = (
        _compute_battery_wh_local(assigned_battery)[2]
        if assigned_battery is not None
        else (first_r.assignedBatteryEnergyWh if first_r else None)
    )

    return DroneBusinessRuleSummary(
        drone_id=drone.id,
        drone_name=drone.name or drone.id,
        drone_model=drone.model or "",
        operational_status=d_status,
        is_available=is_avail,
        max_payload_kg=round(max_kg, 2),
        order_payload_kg=round(float(order_payload_kg or 0.0), 2),
        payload_ok=payload_ok,
        compatible_battery_models=list(compatible_battery_models or []),
        has_compatible_battery=bool(bat_id),
        assigned_battery_id=bat_id,
        assigned_battery_serial=bat_serial,
        assigned_battery_model=bat_model,
        assigned_battery_energy_wh=round(float(bat_energy), 2) if bat_energy is not None else None,
        safety_buffer_pct=20,
        all_routes_count=len(routes_br),
        feasible_routes_count=sum(1 for b in routes_br if b.feasible),
        routes_br=routes_br,
    )


async def build_analyze_drone_explanation(
    db: AsyncSession,
    order: Order,
    drone_obj: Optional[Drone],
    routes_schemas: list[RouteOptionSchema],
    total_payload_kg: float,
    eff_origin_hub_id: int,
    compat_models_map: Optional[dict[str, list[str]]] = None,
    assigned_battery: Optional[Battery] = None,
) -> tuple[Optional[NaturalLanguageShapExplanation], Optional[DroneBusinessRuleSummary]]:
    """
    Sinh `drone_explanation` (SHAP tự nhiên) và `business_rules_summary` cho bước `/mission-planning/analyze`
    kể cả khi Operator giữ mặc định hoặc tự đổi Drone/Pin trên UI.
    """
    if drone_obj is None:
        return None, None

    if compat_models_map is None:
        compat_models_map = await get_compatible_battery_models_map(db)
    compat_models = resolve_compatible_models_from_map(compat_models_map, drone_obj.model)
    br_summary = build_drone_business_rule_summary(
        drone=drone_obj,
        order_payload_kg=total_payload_kg,
        compatible_battery_models=compat_models,
        routes_schemas=routes_schemas,
        assigned_battery=assigned_battery,
    )

    hubs = await get_all_hubs_cached(db)
    hub_dict = {h.id: h for h in hubs}
    origin_hub = hub_dict.get(eff_origin_hub_id)
    d_hub = hub_dict.get(drone_obj.current_hub_id)

    rec_route = next((r for r in routes_schemas if r.recommended and r.feasible), None) or (
        routes_schemas[0] if routes_schemas else None
    )
    is_local = drone_obj.current_hub_id == eff_origin_hub_id
    deadhead_km = 0.0
    if not is_local and d_hub and origin_hub:
        deadhead_km = round(
            haversine_distance(d_hub.latitude or 0.0, d_hub.longitude or 0.0, origin_hub.latitude or 0.0, origin_hub.longitude or 0.0),
            2,
        )

    cap_kg = float(drone_obj.payload_capacity_kg or 0.0)
    fit_ratio = (total_payload_kg / cap_kg) if (cap_kg > 0 and total_payload_kg > 0) else 1.0
    util_pct = float(drone_obj.utilization_pct or 0.0)
    rem_pct = int(rec_route.remainingBatteryPct or 0) if rec_route else 0
    is_feasible_any = br_summary.feasible_routes_count > 0

    reasons_selected: list[str] = []
    if br_summary.is_available:
        reasons_selected.append(f"Operational status is '{br_summary.operational_status}' (ready for mission assignment).")
    else:
        reasons_selected.append(f"Business Rule warning: operational status is '{br_summary.operational_status}' (not Available).")

    if br_summary.payload_ok:
        reasons_selected.append(
            f"Airframe payload capacity ({cap_kg:.1f} kg) satisfies the {total_payload_kg:.2f} kg order load ({min(100, int(round(fit_ratio * 100)))}% load ratio)."
        )
    else:
        reasons_selected.append(
            f"Business Rule violation: max payload ({cap_kg:.2f} kg) is below order weight ({total_payload_kg:.2f} kg)."
        )

    if is_local:
        reasons_selected.append(f"Stationed at departure hub ({origin_hub.name if origin_hub else f'Hub #{eff_origin_hub_id}'}) with 0.0 km positioning flight.")
    else:
        reasons_selected.append(f"Dispatched cross-hub from {d_hub.name if d_hub else f'Hub #{drone_obj.current_hub_id}'} ({deadhead_km:.2f} km deadhead).")

    if br_summary.assigned_battery_id and is_feasible_any:
        reasons_selected.append(
            f"Battery {br_summary.assigned_battery_id} ({br_summary.assigned_battery_model or 'compatible'}, "
            f"{br_summary.assigned_battery_energy_wh or 0:.1f} Wh) clears {br_summary.feasible_routes_count}/{br_summary.all_routes_count} routes "
            f"with >=20% safety buffer ({rem_pct}% reserve on recommended route)."
        )
    elif not br_summary.assigned_battery_id:
        reasons_selected.append(
            f"No compatible active battery ({', '.join(compat_models) or 'N/A'}) available at departure hub."
        )
    else:
        fail_msg = next((b.infeasible_reason for b in br_summary.routes_br if b.infeasible_reason), "Insufficient 20% battery buffer or weather alert")
        reasons_selected.append(f"Route energy/safety check failed on all routes: {fail_msg}")

    # So sánh nhanh tối đa 4 drone khác trong hệ thống để tạo why_not_others
    peers_res = await db.execute(
        select(Drone)
        .where(Drone.id != drone_obj.id)
        .order_by(Drone.current_hub_id == eff_origin_hub_id, Drone.id)
        .limit(30)
    )
    peer_drones = list(peers_res.scalars().all())
    # Ưu tiên local drones trước, rồi đến cross-hub đủ payload
    peer_drones.sort(
        key=lambda pd: (
            0 if pd.current_hub_id == eff_origin_hub_id else 1,
            0 if pd.status == DroneStatus.AVAILABLE else 1,
            0 if float(pd.payload_capacity_kg or 0.0) >= total_payload_kg else 1,
            pd.id,
        )
    )

    why_not_others: list[AlternativeComparisonItem] = []
    for pd in peer_drones[:5]:
        pd_status = pd.status.value if hasattr(pd.status, "value") else str(pd.status or "")
        pd_cap = float(pd.payload_capacity_kg or 0.0)
        pd_hub = hub_dict.get(pd.current_hub_id)
        pd_compat = resolve_compatible_models_from_map(compat_models_map, pd.model)
        pd_label = f"{pd.id} ({pd.name} - {pd.model})"
        if pd_status != DroneStatus.AVAILABLE.value:
            pd_feasible = False
            pd_reason = f"Disqualified: Drone status is '{pd_status}' (not Available)."
        elif pd_cap < total_payload_kg:
            pd_feasible = False
            pd_reason = f"Disqualified: Max payload ({pd_cap:.2f} kg) is below order payload ({total_payload_kg:.2f} kg)."
        elif pd.current_hub_id != eff_origin_hub_id and is_local:
            pd_feasible = True
            pd_reason = f"Alternative at {pd_hub.name if pd_hub else f'Hub #{pd.current_hub_id}'}: requires empty cross-hub positioning flight."
        else:
            pd_feasible = True
            pd_reason = (
                f"Alternative at {pd_hub.name if pd_hub else f'Hub #{pd.current_hub_id}'}: "
                f"{pd_cap:.1f} kg capacity, compatible batteries [{', '.join(pd_compat) or 'N/A'}], "
                f"{float(pd.utilization_pct or 0.0):.1f}% fleet utilization."
            )
        why_not_others.append(
            AlternativeComparisonItem(
                id=pd.id,
                label=pd_label,
                feasible=pd_feasible,
                reason=pd_reason,
            )
        )

    summary = (
        f"Drone {drone_obj.id} ({drone_obj.name}, {cap_kg:.1f} kg, status '{br_summary.operational_status}') "
        f"supports compatible batteries [{', '.join(compat_models) or 'Standard'}] and clears "
        f"{br_summary.feasible_routes_count}/{br_summary.all_routes_count} route options with a 20% safety reserve."
    )

    return (
        NaturalLanguageShapExplanation(
            selected_id=drone_obj.id,
            selected_label=f"{drone_obj.id} ({drone_obj.name} - {drone_obj.model})",
            summary=summary,
            reasons_selected=reasons_selected,
            why_not_others=why_not_others,
        ),
        br_summary,
    )


async def _evaluate_fleet_for_order(
    db: AsyncSession,
    order_id: str,
    bundled_order_ids: Optional[list[str]],
    recommend_mode: Optional[str],
    compute_routes_fn: Callable[..., Awaitable[tuple[list[RouteOptionSchema], list[dict]]]],
    get_reserved_batteries_fn: Callable[..., Awaitable[tuple[set[str], dict[int, int]]]],
    eval_all_local: bool = False,
) -> dict[str, Any]:
    """
    Lõi dùng chung đánh giá đội Drone, Pin và Tuyến bay cho cả Recommend Wizard và Full Auto-Dispatch.
    [PERF NOTE] Bắt buộc dùng `get_all_hubs_cached`, gộp `busy_drone_ids` vào `get_reserved_batteries_fn`,
    và truyền `shared_route_ctx` cho `compute_routes_fn` để không lặp query DB/thời tiết cho từng drone.
    """
    order_missions: list[Mission] = []
    order, clean_bundle_ids, total_payload_kg, total_fee = await _validate_order_and_bundle_for_auto_dispatch(
        db=db,
        order_id=order_id,
        bundled_order_ids=bundled_order_ids,
        order_missions_out=order_missions,
    )

    eff_origin_hub_id, is_replan = resolve_effective_origin_from_missions(order, order_missions)
    if not eff_origin_hub_id or not order.destination_hub_id:
        raise HTTPException(status_code=400, detail="Order is missing Origin Hub or Destination Hub information")

    all_hubs = await get_all_hubs_cached(db)
    all_hubs_map = {h.id: h for h in all_hubs}
    origin_hub = all_hubs_map.get(eff_origin_hub_id)
    dest_hub = all_hubs_map.get(order.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=400, detail="Origin Hub or Destination Hub does not exist in the system")

    req_mode = (recommend_mode or "AUTO").strip().upper()
    order_mode_raw = (
        order.delivery_mode.value
        if hasattr(order.delivery_mode, "value")
        else str(order.delivery_mode or "NORMAL")
    ).strip().upper()
    optimization_objective = (
        req_mode if req_mode in ("ECO", "FAST")
        else ("FAST" if order_mode_raw == DeliveryMode.EXPRESS.value else "ECO")
    )

    active_hubs = [h for h in all_hubs if str(h.status or "").lower() == "active"] or list(all_hubs)
    hub_dict = {h.id: h for h in active_hubs}
    hub_dict.setdefault(origin_hub.id, origin_hub)
    hub_dict.setdefault(dest_hub.id, dest_hub)

    busy_drone_ids: set[str] = set()
    reserved_kwargs: dict[str, Any] = {
        "db": db,
        "relay_hub_ids": list(hub_dict.keys()),
        "compatible_models": [],
        "exclude_order_id": order.id,
    }
    supports_busy_out = "busy_drone_ids_out" in inspect.signature(get_reserved_batteries_fn).parameters
    if supports_busy_out:
        reserved_kwargs["busy_drone_ids_out"] = busy_drone_ids

    reserved_bat_ids, held_count_by_hub = await get_reserved_batteries_fn(**reserved_kwargs)
    if not supports_busy_out:
        busy_drones_res = await db.execute(
            select(Mission.drone_id).where(
                Mission.drone_id.is_not(None),
                Mission.order_id != order.id,
                Mission.status.in_(ACTIVE_MISSION_STATUSES),
            )
        )
        busy_drone_ids = {r[0] for r in busy_drones_res.all() if r[0] is not None}

    all_idle_drones_res = await db.execute(
        select(Drone).where(Drone.status == DroneStatus.AVAILABLE).order_by(Drone.id)
    )
    all_idle_drones = [d for d in all_idle_drones_res.scalars().all() if d.id not in busy_drone_ids]
    under_payload_drones = [
        d for d in all_idle_drones
        if float(d.payload_capacity_kg or 0.0) < total_payload_kg
    ]
    candidate_drones = [
        d for d in all_idle_drones
        if float(d.payload_capacity_kg or 0.0) >= total_payload_kg
    ]
    if not candidate_drones:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No available idle drone in the fleet can carry the payload of {total_payload_kg:.2f} kg."
            ),
        )

    all_active_bats = list(
        (await db.execute(select(Battery).where(Battery.status == batteries_status.ACTIVE).order_by(Battery.id))).scalars().all()
    )

    local_drones = [d for d in candidate_drones if d.current_hub_id == eff_origin_hub_id]
    other_drones = [d for d in candidate_drones if d.current_hub_id != eff_origin_hub_id]
    other_drones.sort(
        key=lambda d: haversine_distance(
            (hub_dict.get(d.current_hub_id).latitude if hub_dict.get(d.current_hub_id) else 0.0) or 0.0,
            (hub_dict.get(d.current_hub_id).longitude if hub_dict.get(d.current_hub_id) else 0.0) or 0.0,
            origin_hub.latitude or 0.0,
            origin_hub.longitude or 0.0,
        )
    )

    compat_cache: dict[str, list[str]] = await get_compatible_battery_models_map(db)
    supports_preloaded_ctx = "preloaded_context" in inspect.signature(compute_routes_fn).parameters
    shared_route_ctx: dict[str, Any] = {
        "eff_origin_hub_id": eff_origin_hub_id,
        "hubs": list(hub_dict.values()),
        "all_active_batteries": all_active_bats,
        "reserved_battery_ids": reserved_bat_ids,
        "held_count_by_hub": held_count_by_hub,
        "compat_models_map": compat_cache,
        "drones_by_id": {d.id: d for d in candidate_drones},
        "seg_weather_cache": {},
    }

    async def _get_drone_elig_bats(d: Drone) -> list[Battery]:
        compat_models = resolve_compatible_models_from_map(compat_cache, d.model)
        d_hub_id = d.current_hub_id or eff_origin_hub_id
        return [
            b for b in all_active_bats
            if b.id not in reserved_bat_ids
            and (b.charge_level_pct or 0) > 0
            and (b.drone_id == d.id or (b.drone_id is None and b.current_hub_id == d_hub_id))
            and (not compat_models or (b.battery_model and b.battery_model in compat_models))
        ]

    route_group_cache: dict[tuple, tuple[str, Optional[str], Optional[str], list[RouteOptionSchema], list[dict]]] = {}

    def _pick_best_bat_for_drone(d: Drone, bats_list: list[Battery]) -> Optional[Battery]:
        if not bats_list:
            return None
        return min(
            bats_list,
            key=lambda b: (
                -(_compute_battery_wh_local(b)[2]),
                0 if b.drone_id == d.id else 1,
                -(b.charge_level_pct or 0),
                -float(b.capacity_wh or 0.0),
                b.id,
            ),
        )

    def _clone_routes_for_drone(
        cached_entry: tuple[str, Optional[str], Optional[str], list[RouteOptionSchema], list[dict]],
        target_drone: Drone,
        target_bat: Optional[Battery],
    ) -> tuple[list[RouteOptionSchema], list[dict]]:
        tmpl_did, tmpl_bid, tmpl_bser, tmpl_schemas, tmpl_raw = cached_entry
        cloned_schemas = copy.deepcopy(tmpl_schemas)
        cloned_raw = copy.deepcopy(tmpl_raw)
        tgt_bid = target_bat.id if target_bat else None
        tgt_bser = target_bat.serial_number if target_bat else None

        for s in cloned_schemas:
            old_did = str(s.suggestedDroneId) if s.suggestedDroneId is not None else tmpl_did
            old_bid = s.assignedBatteryId or tmpl_bid
            old_bser = s.assignedBatterySerial or tmpl_bser
            if s.suggestedDroneId == tmpl_did or old_did == tmpl_did:
                s.suggestedDroneId = target_drone.id
                if s.assignedBatteryId == tmpl_bid and tgt_bid:
                    s.assignedBatteryId = tgt_bid
                    s.assignedBatterySerial = tgt_bser
                if s.legs_detail and s.legs_detail[0].battery_id == old_bid and tgt_bid:
                    s.legs_detail[0].battery_id = tgt_bid
                    s.legs_detail[0].battery_serial = tgt_bser
                if s.reason:
                    if tmpl_did and target_drone.id != tmpl_did:
                        s.reason = s.reason.replace(tmpl_did, target_drone.id)
                    if old_bid and tgt_bid and tgt_bid != old_bid:
                        s.reason = s.reason.replace(old_bid, tgt_bid)
                    if old_bser and tgt_bser and tgt_bser != old_bser:
                        s.reason = s.reason.replace(old_bser, tgt_bser)
        return cloned_schemas, cloned_raw

    async def _evaluate_drone(d: Drone, preloaded_bats: Optional[list[Battery]] = None) -> Optional[dict[str, Any]]:
        elig_bats = preloaded_bats if preloaded_bats is not None else await _get_drone_elig_bats(d)
        is_local = d.current_hub_id == eff_origin_hub_id
        d_hub = hub_dict.get(d.current_hub_id)
        deadhead_km = (
            0.0 if is_local or not d_hub
            else round(
                haversine_distance(
                    d_hub.latitude or 0.0, d_hub.longitude or 0.0,
                    origin_hub.latitude or 0.0, origin_hub.longitude or 0.0,
                ),
                2,
            )
        )

        best_bat = _pick_best_bat_for_drone(d, elig_bats)
        if best_bat is not None:
            _, b_pct, b_avail_wh = _compute_battery_wh_local(best_bat)
            bat_spec: Any = (best_bat.battery_model, round(b_avail_wh, 1), b_pct)
        else:
            bat_spec = None
        # Khóa nhóm chặt theo Hub, dòng máy bay, tải trọng và thông số pin tốt nhất để không sai lệch dự phòng chặng 1
        group_key = (
            d.current_hub_id,
            d.model or "",
            round(float(d.payload_capacity_kg or 0.0), 2),
            bat_spec,
        )

        if supports_preloaded_ctx and group_key in route_group_cache:
            routes_schemas, raw_list = _clone_routes_for_drone(route_group_cache[group_key], d, best_bat)
        else:
            route_kwargs: dict[str, Any] = {
                "db": db,
                "order": order,
                "drone_id": d.id,
                "battery_id": None,
                "strict_battery": False,
                "override_payload_kg": total_payload_kg,
                "recommend_mode": recommend_mode or "AUTO",
            }
            if supports_preloaded_ctx:
                route_kwargs["preloaded_context"] = shared_route_ctx
            routes_schemas, raw_list = await compute_routes_fn(**route_kwargs)
            if supports_preloaded_ctx and routes_schemas:
                ref_s = routes_schemas[0]
                if str(ref_s.suggestedDroneId or d.id) == str(d.id):
                    route_group_cache[group_key] = (
                        str(d.id),
                        ref_s.assignedBatteryId,
                        ref_s.assignedBatterySerial,
                        routes_schemas,
                        raw_list,
                    )
        feasible_pairs = [(s, raw) for s, raw in zip(routes_schemas, raw_list) if s.feasible]
        chosen_pair = (
            next(((s, raw) for s, raw in feasible_pairs if s.recommended), None)
            or (feasible_pairs[0] if feasible_pairs else None)
        )
        is_feasible = chosen_pair is not None and bool(elig_bats)
        rec_route = chosen_pair[0] if chosen_pair else (routes_schemas[0] if routes_schemas else None)
        rec_raw = chosen_pair[1] if chosen_pair else (raw_list[0] if raw_list else {})

        cap_kg = float(d.payload_capacity_kg or 0.0)
        fit_ratio = (total_payload_kg / cap_kg) if (cap_kg > 0 and total_payload_kg > 0) else 1.0
        util_pct = float(d.utilization_pct or 0.0)

        u_locality = 1.0 if is_local else max(0.1, 0.55 - min(deadhead_km, 50.0) / 100.0)
        u_payload = min(1.0, 0.35 + 0.65 * fit_ratio) if total_payload_kg > 0 else 1.0
        u_util = max(0.0, 1.0 - min(1.0, util_pct / 100.0))

        if is_feasible and rec_route:
            has_installed_ready = any(b.drone_id == d.id and (b.charge_level_pct or 0) >= 100 for b in elig_bats)
            reserve_frac = max(0.0, min(1.0, float(rec_route.remainingBatteryPct or 0) / 100.0))
            u_readiness = min(1.0, 0.55 + 0.35 * reserve_frac + (0.10 if has_installed_ready else 0.0))
            readiness_raw = f"Feasible ({rec_route.remainingBatteryPct}% reserve, {len(elig_bats)} ready batteries)"
            readiness_impact = (
                f"Clears all route energy & weather safety checks with {rec_route.remainingBatteryPct}% post-flight reserve"
            )
        else:
            u_readiness = 0.0
            infeas_msg = (rec_route.infeasible_reason if rec_route else None) or "No compatible charged battery available"
            readiness_raw = f"Infeasible ({len(elig_bats)} compatible batteries)"
            readiness_impact = f"Disqualified: {infeas_msg}"

        weighted_score = round(
            35.0 * u_locality + 25.0 * u_payload + 25.0 * u_readiness + 15.0 * u_util,
            2,
        )
        return {
            "drone": d,
            "id": d.id,
            "label": f"{d.id} ({d.name} - {d.model})",
            "hub_name": d_hub.name if d_hub else None,
            "deadhead_km": deadhead_km,
            "is_local": is_local,
            "is_feasible": is_feasible,
            "weighted_score": weighted_score,
            "elig_bats": elig_bats,
            "routes_schemas": routes_schemas,
            "raw_list": raw_list,
            "rec_route": rec_route,
            "rec_raw": rec_raw,
            "infeasible_reason": rec_route.infeasible_reason if rec_route else "No compatible route/battery",
            "utilities": {
                "hub_locality": u_locality,
                "payload_fit": u_payload,
                "energy_readiness": u_readiness,
                "fleet_utilization_balance": u_util,
            },
            "raw_values": {
                "hub_locality": (
                    f"{origin_hub.name} (0.0 km deadhead)"
                    if is_local
                    else f"{d_hub.name if d_hub else f'Hub #{d.current_hub_id}'} ({deadhead_km:.2f} km away)"
                ),
                "payload_fit": f"{total_payload_kg:.2f} / {cap_kg:.2f} kg ({fit_ratio * 100:.0f}% utilization)",
                "energy_readiness": readiness_raw,
                "fleet_utilization_balance": f"{util_pct:.1f}% fleet wear utilization",
            },
            "impacts": {
                "hub_locality": (
                    f"Stationed directly at departure hub ({origin_hub.name}) — avoids empty positioning flight"
                    if is_local
                    else f"Requires {deadhead_km:.2f} km positioning flight from {d_hub.name if d_hub else f'Hub #{d.current_hub_id}'}"
                ),
                "payload_fit": (
                    f"Airframe capacity ({cap_kg:.1f} kg) matches the {total_payload_kg:.2f} kg payload efficiently ({fit_ratio * 100:.0f}% load ratio)"
                ),
                "energy_readiness": readiness_impact,
                "fleet_utilization_balance": f"Balances fleet wear across drones (current utilization {util_pct:.1f}%)",
            },
        }

    local_scored: list[tuple[tuple, Drone, list[Battery]]] = []
    for d in local_drones:
        ebats = await _get_drone_elig_bats(d)
        cap_kg = float(d.payload_capacity_kg or 0.0)
        fit_ratio = (total_payload_kg / cap_kg) if (cap_kg > 0 and total_payload_kg > 0) else 1.0
        has_100 = any((b.charge_level_pct or 0) >= 100 for b in ebats)
        has_inst = any(b.drone_id == d.id for b in ebats)
        max_wh = max((_compute_battery_wh_local(b)[2] for b in ebats), default=0.0)
        local_scored.append((
            (1 if ebats else 0, 1 if has_100 else 0, 1 if has_inst else 0, round(fit_ratio, 3), max_wh, -float(d.utilization_pct or 0.0)),
            d,
            ebats,
        ))
    local_scored.sort(key=lambda item: item[0], reverse=True)

    evaluated_drones: list[dict[str, Any]] = []
    ready_local = [(d, bats) for _, d, bats in local_scored if bats]
    local_limit = len(ready_local) if eval_all_local else 5
    for d, bats in ready_local[:local_limit]:
        ev = await _evaluate_drone(d, preloaded_bats=bats)
        if ev:
            evaluated_drones.append(ev)

    feasible_drones = [e for e in evaluated_drones if e["is_feasible"]]
    if not feasible_drones and not eval_all_local and len(ready_local) > 5:
        for d, bats in ready_local[5:]:
            ev = await _evaluate_drone(d, preloaded_bats=bats)
            if ev:
                evaluated_drones.append(ev)
        feasible_drones = [e for e in evaluated_drones if e["is_feasible"]]

    if not feasible_drones or eval_all_local:
        cross_checked = 0
        cross_limit = 3 if not feasible_drones else 2
        for od in other_drones:
            obats = await _get_drone_elig_bats(od)
            if not obats:
                continue
            ev = await _evaluate_drone(od, preloaded_bats=obats)
            if ev:
                evaluated_drones.append(ev)
            cross_checked += 1
            if cross_checked >= cross_limit:
                break
        feasible_drones = [e for e in evaluated_drones if e["is_feasible"]]

    if not feasible_drones:
        if not evaluated_drones:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Found {len(candidate_drones)} idle drone(s) matching the payload, "
                    "but none currently have an unreserved compatible active battery (>0% charge) at their hub."
                ),
            )
        sample_reason = next(
            (e["infeasible_reason"] for e in evaluated_drones if e.get("infeasible_reason")),
            "No compatible battery or feasible route met the 20% safety reserve / weather limit.",
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Evaluated {len(evaluated_drones)} eligible drone(s), "
                f"but none produced a feasible route. Reason: {sample_reason}"
            ),
        )

    best_drone_idx = max(
        range(len(evaluated_drones)),
        key=lambda i: (
            1 if evaluated_drones[i]["is_feasible"] else 0,
            1 if evaluated_drones[i]["is_local"] else 0,
            evaluated_drones[i]["weighted_score"],
            -i,
        ),
    )
    chosen_d_eval = evaluated_drones[best_drone_idx]
    best_drone: Drone = chosen_d_eval["drone"]
    best_route: RouteOptionSchema = chosen_d_eval["rec_route"]
    available_routes: list[RouteOptionSchema] = chosen_d_eval["routes_schemas"]
    raw_list: list[dict] = chosen_d_eval["raw_list"]

    req_leg1_wh = (
        float(best_route.legs_detail[0].energy_wh)
        if best_route.legs_detail
        else float(best_route.predictedEnergyWh or 0.0)
    )
    bat_candidates, best_bat_idx, battery_shap = _evaluate_battery_candidates_list(
        compatible_bats=chosen_d_eval["elig_bats"],
        drone=best_drone,
        req_leg1_wh=req_leg1_wh,
    )
    best_battery: Battery = bat_candidates[best_bat_idx]["battery"]

    if best_route.assignedBatteryId != best_battery.id:
        recheck_kwargs: dict[str, Any] = {
            "db": db,
            "order": order,
            "drone_id": best_drone.id,
            "battery_id": best_battery.id,
            "strict_battery": True,
            "override_payload_kg": total_payload_kg,
            "recommend_mode": recommend_mode or "AUTO",
        }
        if supports_preloaded_ctx:
            recheck_kwargs["preloaded_context"] = shared_route_ctx
        recheck_schemas, recheck_raw = await compute_routes_fn(**recheck_kwargs)
        recheck_feasible = [s for s in recheck_schemas if s.feasible]
        if recheck_feasible:
            available_routes = recheck_schemas
            raw_list = recheck_raw
            best_route = next((s for s in recheck_feasible if s.recommended), recheck_feasible[0])

    drone_feature_specs = [
        ("hub_locality", "Station Proximity (Deadhead Avoidance)", 35.0),
        ("payload_fit", "Payload Capacity Fit", 25.0),
        ("energy_readiness", "Energy & Route Feasibility", 25.0),
        ("fleet_utilization_balance", "Fleet Wear Balancing", 15.0),
    ]
    why_drone = (
        f"Selected Drone {best_drone.id} ({best_drone.name}, capacity {best_drone.payload_capacity_kg:.1f} kg) "
        + (f"stationed at {origin_hub.name} " if chosen_d_eval["is_local"] else f"via Cross-Hub Dispatch from {chosen_d_eval['hub_name'] or f'Hub #{best_drone.current_hub_id}'} ")
        + f"with utility score {chosen_d_eval['weighted_score']:.2f}/100."
    )
    drone_shap = _compute_additive_shap(
        candidates=evaluated_drones,
        selected_idx=best_drone_idx,
        feature_specs=drone_feature_specs,
        why_selected=why_drone,
        counterfactual_builder=lambda c, delta: {
            "candidate_id": c["id"],
            "candidate_label": c["label"],
            "score": c["weighted_score"],
            "score_delta_vs_selected": delta,
            "feasible": c["is_feasible"],
            "comparison_note": _compare_drone_vs_winner(c, chosen_d_eval, total_payload_kg),
        },
    )

    return {
        "order": order,
        "clean_bundle_ids": clean_bundle_ids,
        "total_payload_kg": total_payload_kg,
        "total_fee": total_fee,
        "eff_origin_hub_id": eff_origin_hub_id,
        "origin_hub": origin_hub,
        "hub_dict": hub_dict,
        "is_replan": is_replan,
        "order_mode_raw": order_mode_raw,
        "optimization_objective": optimization_objective,
        "evaluated_drones": evaluated_drones,
        "best_drone_idx": best_drone_idx,
        "chosen_d_eval": chosen_d_eval,
        "best_drone": best_drone,
        "under_payload_drones": under_payload_drones,
        "local_no_bat_drones": [
            d for d in local_drones if all(ev["id"] != d.id for ev in evaluated_drones)
        ],
        "bat_candidates": bat_candidates,
        "best_bat_idx": best_bat_idx,
        "best_battery": best_battery,
        "req_leg1_wh": req_leg1_wh,
        "best_route": best_route,
        "available_routes": available_routes,
        "raw_list": raw_list,
        "drone_shap": drone_shap,
        "battery_shap": battery_shap,
        "compat_cache": compat_cache,
    }


async def recommend_resources_for_order(
    db: AsyncSession,
    order_id: str,
    bundled_order_ids: Optional[list[str]],
    compute_routes_fn: Callable[..., Awaitable[tuple[list[RouteOptionSchema], list[dict]]]],
    get_reserved_batteries_fn: Callable[..., Awaitable[tuple[set[str], dict[int, int]]]],
) -> RecommendResourcesResponse:
    """
    Bước 1 của quy trình lập kế hoạch bán tự động:
    Tự động gợi ý Drone & Pin tối ưu nhất kèm giải thích XAI SHAP bằng tiếng Anh tự nhiên
    (không hiển thị điểm số thô) và liệt kê đầy đủ Drone/Pin khả dụng để Operator tự do đổi.
    """
    ctx = await _evaluate_fleet_for_order(
        db=db,
        order_id=order_id,
        bundled_order_ids=bundled_order_ids,
        recommend_mode="AUTO",
        compute_routes_fn=compute_routes_fn,
        get_reserved_batteries_fn=get_reserved_batteries_fn,
        eval_all_local=True,
    )

    order: Order = ctx["order"]
    origin_hub: Hub = ctx["origin_hub"]
    hub_dict: dict[int, Hub] = ctx["hub_dict"]
    evaluated_drones: list[dict[str, Any]] = ctx["evaluated_drones"]
    chosen_d_eval: dict[str, Any] = ctx["chosen_d_eval"]
    best_drone: Drone = ctx["best_drone"]
    bat_candidates: list[dict[str, Any]] = ctx["bat_candidates"]
    best_bat_idx: int = ctx["best_bat_idx"]
    best_battery: Battery = ctx["best_battery"]
    req_leg1_wh: float = ctx["req_leg1_wh"]
    drone_shap: DecisionShapExplanation = ctx["drone_shap"]
    battery_shap: DecisionShapExplanation = ctx["battery_shap"]
    total_payload_kg: float = ctx["total_payload_kg"]

    # 1. Giải thích bằng chữ cho Drone được chọn & lý do loại các Drone khác
    drone_reasons = _extract_top_shap_reasons(drone_shap)
    drone_summary = (
        f"Recommended Drone {best_drone.id} ({best_drone.name}, {float(best_drone.payload_capacity_kg or 0):.1f} kg capacity) "
        + (f"because it is stationed at {origin_hub.name} with zero positioning flight, " if chosen_d_eval["is_local"] else f"via cross-hub dispatch from {chosen_d_eval['hub_name'] or f'Hub #{best_drone.current_hub_id}'}, ")
        + f"fits the {total_payload_kg:.2f} kg payload efficiently, and clears energy safety checks with {ctx['best_route'].remainingBatteryPct}% reserve."
    )

    drone_why_not: list[AlternativeComparisonItem] = []
    for idx, ev in enumerate(evaluated_drones):
        if idx == ctx["best_drone_idx"]:
            continue
        drone_why_not.append(
            AlternativeComparisonItem(
                id=ev["id"],
                label=ev["label"],
                feasible=ev["is_feasible"],
                reason=_compare_drone_vs_winner(ev, chosen_d_eval, total_payload_kg),
            )
        )
    for ud in ctx["under_payload_drones"]:
        drone_why_not.append(
            AlternativeComparisonItem(
                id=ud.id,
                label=f"{ud.id} ({ud.name} - {ud.model})",
                feasible=False,
                reason=(
                    f"Disqualified: Maximum payload capacity ({float(ud.payload_capacity_kg or 0):.2f} kg) "
                    f"is below the order payload ({total_payload_kg:.2f} kg)."
                ),
            )
        )
    for nbd in ctx["local_no_bat_drones"]:
        drone_why_not.append(
            AlternativeComparisonItem(
                id=nbd.id,
                label=f"{nbd.id} ({nbd.name} - {nbd.model})",
                feasible=False,
                reason="Disqualified: No unreserved compatible active battery (>0% charge) available at the departure hub.",
            )
        )

    drone_nl_expl = NaturalLanguageShapExplanation(
        selected_id=best_drone.id,
        selected_label=chosen_d_eval["label"],
        summary=drone_summary,
        reasons_selected=drone_reasons,
        why_not_others=drone_why_not,
    )

    # 2. Giải thích bằng chữ cho Pin được chọn & lý do loại các Pin khác
    bat_reasons = _extract_top_shap_reasons(battery_shap)
    chosen_bat_entry = bat_candidates[best_bat_idx]
    bat_summary = (
        f"Recommended Battery {best_battery.id} ({best_battery.serial_number}, {chosen_bat_entry['charge_pct']}% SoC / {chosen_bat_entry['avail_wh']:.1f} Wh usable) "
        + ("because it is already pre-mounted on the drone, " if chosen_bat_entry["is_installed"] else "because it is available at the departure hub, ")
        + f"comfortably covers Leg 1 demand ({req_leg1_wh:.1f} Wh) with >=20% reserve, and maintains healthy lifecycle standing."
    )
    bat_why_not: list[AlternativeComparisonItem] = [
        AlternativeComparisonItem(
            id=bc["id"],
            label=bc["label"],
            feasible=bool(bc["meets_reserve"] and not bc.get("is_expired")),
            reason=_compare_battery_vs_winner(bc, chosen_bat_entry, best_drone, req_leg1_wh),
        )
        for b_idx, bc in enumerate(bat_candidates)
        if b_idx != best_bat_idx
    ]
    bat_nl_expl = NaturalLanguageShapExplanation(
        selected_id=best_battery.id,
        selected_label=chosen_bat_entry["label"],
        summary=bat_summary,
        reasons_selected=bat_reasons,
        why_not_others=bat_why_not,
    )

    # 3. Đóng gói danh sách Drone & Pin để Operator tự do thay đổi trên UI
    def _to_battery_options(d_obj: Drone, bats_list: list[Battery], leg1_demand_wh: float, selected_bat_id: Optional[str]) -> list[CandidateBatteryOption]:
        if not bats_list:
            return []
        bc_list, b_best_idx, _ = _evaluate_battery_candidates_list(bats_list, d_obj, leg1_demand_wh)
        win_bc = bc_list[b_best_idx]
        indexed = list(enumerate(bc_list))
        indexed.sort(key=lambda pair: (0 if pair[0] == b_best_idx else 1, -pair[1]["weighted_score"]))
        opts: list[CandidateBatteryOption] = []
        for idx_b, bc in indexed:
            b_obj: Battery = bc["battery"]
            is_rec = (b_obj.id == selected_bat_id) if selected_bat_id else (idx_b == b_best_idx)
            is_b_feasible = bool(bc["meets_reserve"] and not bc.get("is_expired"))
            opts.append(
                CandidateBatteryOption(
                    id=b_obj.id,
                    serial_number=b_obj.serial_number,
                    model=b_obj.battery_model,
                    capacity_wh=round(bc["cap_wh"], 1),
                    charge_level_pct=bc["charge_pct"],
                    available_energy_wh=bc["avail_wh"],
                    is_pre_mounted=bool(bc["is_installed"]),
                    is_recommended=is_rec,
                    feasible=is_b_feasible,
                    infeasible_reason=(
                        None if is_b_feasible
                        else _compare_battery_vs_winner(bc, win_bc, d_obj, leg1_demand_wh)
                    ),
                    selection_reason=(
                        bat_summary if is_rec
                        else _compare_battery_vs_winner(bc, win_bc, d_obj, leg1_demand_wh)
                    ),
                )
            )
        return opts

    available_drones: list[CandidateDroneOption] = []
    sorted_eval_pairs = list(enumerate(evaluated_drones))
    sorted_eval_pairs.sort(
        key=lambda pair: (
            0 if pair[0] == ctx["best_drone_idx"] else 1,
            0 if pair[1]["is_feasible"] else 1,
            0 if pair[1]["is_local"] else 1,
            -pair[1]["weighted_score"],
        )
    )
    for ev_idx, ev in sorted_eval_pairs:
        d_obj = ev["drone"]
        is_d_rec = (ev_idx == ctx["best_drone_idx"])
        ev_route = ev["rec_route"]
        d_leg1_wh = (
            float(ev_route.legs_detail[0].energy_wh)
            if (ev_route and ev_route.legs_detail)
            else float(ev_route.predictedEnergyWh if ev_route else req_leg1_wh)
        )
        bat_opts = _to_battery_options(
            d_obj=d_obj,
            bats_list=ev["elig_bats"],
            leg1_demand_wh=d_leg1_wh,
            selected_bat_id=(best_battery.id if is_d_rec else None),
        )
        d_status_str = d_obj.status.value if hasattr(d_obj.status, "value") else str(d_obj.status or "")
        d_compat_models = resolve_compatible_models_from_map(ctx["compat_cache"], d_obj.model)
        d_route_brs = [
            build_route_business_rule_check(
                route=r_s,
                payload_ok=float(d_obj.payload_capacity_kg or 0.0) >= total_payload_kg,
                is_available=(d_status_str == DroneStatus.AVAILABLE.value),
                drone_status_label=d_status_str,
                order_payload_kg=total_payload_kg,
                max_payload_kg=float(d_obj.payload_capacity_kg or 0.0),
            )
            for r_s in (ev.get("routes_schemas") or [])
        ]
        available_drones.append(
            CandidateDroneOption(
                id=d_obj.id,
                name=d_obj.name or d_obj.id,
                model=d_obj.model or "",
                operational_status=d_status_str,
                current_hub_id=d_obj.current_hub_id,
                current_hub_name=ev["hub_name"],
                is_local=ev["is_local"],
                deadhead_km=ev["deadhead_km"],
                payload_capacity_kg=float(d_obj.payload_capacity_kg or 0.0),
                compatible_battery_models=d_compat_models,
                is_recommended=is_d_rec,
                feasible=ev["is_feasible"],
                infeasible_reason=None if ev["is_feasible"] else ev["infeasible_reason"],
                selection_reason=(
                    drone_summary if is_d_rec
                    else _compare_drone_vs_winner(ev, chosen_d_eval, total_payload_kg)
                ),
                eligible_batteries=bat_opts,
                route_br_checks=d_route_brs,
            )
        )

    for nbd in ctx["local_no_bat_drones"]:
        d_hub = hub_dict.get(nbd.current_hub_id)
        nbd_status = nbd.status.value if hasattr(nbd.status, "value") else str(nbd.status or "")
        nbd_compat = resolve_compatible_models_from_map(ctx["compat_cache"], nbd.model)
        available_drones.append(
            CandidateDroneOption(
                id=nbd.id,
                name=nbd.name or nbd.id,
                model=nbd.model or "",
                operational_status=nbd_status,
                current_hub_id=nbd.current_hub_id,
                current_hub_name=d_hub.name if d_hub else None,
                is_local=True,
                deadhead_km=0.0,
                payload_capacity_kg=float(nbd.payload_capacity_kg or 0.0),
                compatible_battery_models=nbd_compat,
                is_recommended=False,
                feasible=False,
                infeasible_reason="No unreserved compatible active battery (>0% charge) available at departure hub.",
                selection_reason="Disqualified: No unreserved compatible active battery available at departure hub.",
                eligible_batteries=[],
                route_br_checks=[],
            )
        )

    for ud in ctx["under_payload_drones"]:
        d_hub = hub_dict.get(ud.current_hub_id)
        is_u_local = ud.current_hub_id == ctx["eff_origin_hub_id"]
        ud_status = ud.status.value if hasattr(ud.status, "value") else str(ud.status or "")
        ud_compat = resolve_compatible_models_from_map(ctx["compat_cache"], ud.model)
        reason_str = (
            f"Maximum payload capacity ({float(ud.payload_capacity_kg or 0):.2f} kg) "
            f"is below order payload ({total_payload_kg:.2f} kg)."
        )
        available_drones.append(
            CandidateDroneOption(
                id=ud.id,
                name=ud.name or ud.id,
                model=ud.model or "",
                operational_status=ud_status,
                current_hub_id=ud.current_hub_id,
                current_hub_name=d_hub.name if d_hub else None,
                is_local=is_u_local,
                deadhead_km=0.0,
                payload_capacity_kg=float(ud.payload_capacity_kg or 0.0),
                compatible_battery_models=ud_compat,
                is_recommended=False,
                feasible=False,
                infeasible_reason=reason_str,
                selection_reason=f"Disqualified: {reason_str}",
                eligible_batteries=[],
                route_br_checks=[],
            )
        )

    return RecommendResourcesResponse(
        order_id=order.id,
        bundled_order_ids=ctx["clean_bundle_ids"],
        total_payload_kg=total_payload_kg,
        effective_origin_hub_id=ctx["eff_origin_hub_id"],
        effective_origin_hub_name=origin_hub.name,
        is_replan_dispatch=ctx["is_replan"],
        is_cross_hub_dispatch=not chosen_d_eval["is_local"],
        recommended_drone_id=best_drone.id,
        recommended_drone_name=best_drone.name or best_drone.id,
        recommended_drone_model=best_drone.model or "",
        recommended_battery_id=best_battery.id,
        recommended_battery_serial=best_battery.serial_number,
        drone_explanation=drone_nl_expl,
        battery_explanation=bat_nl_expl,
        available_drones=available_drones,
    )


async def run_auto_dispatch_selection(
    db: AsyncSession,
    order_id: str,
    bundled_order_ids: Optional[list[str]],
    recommend_mode: Optional[str],
    compute_routes_fn: Callable[..., Awaitable[tuple[list[RouteOptionSchema], list[dict]]]],
    get_reserved_batteries_fn: Callable[..., Awaitable[tuple[set[str], dict[int, int]]]],
) -> dict[str, Any]:
    """
    Thực hiện chọn lựa tối ưu hoàn chỉnh (Drone -> Battery -> Route) và tạo báo cáo XAI SHAP.
    Trả về dict gồm toàn bộ dữ liệu để tạo Mission (nếu dry_run=False) và AutoDispatchResponse.
    """
    ctx = await _evaluate_fleet_for_order(
        db=db,
        order_id=order_id,
        bundled_order_ids=bundled_order_ids,
        recommend_mode=recommend_mode,
        compute_routes_fn=compute_routes_fn,
        get_reserved_batteries_fn=get_reserved_batteries_fn,
        eval_all_local=False,
    )

    order: Order = ctx["order"]
    best_drone: Drone = ctx["best_drone"]
    best_battery: Battery = ctx["best_battery"]
    best_route: RouteOptionSchema = ctx["best_route"]
    available_routes: list[RouteOptionSchema] = ctx["available_routes"]
    raw_list: list[dict] = ctx["raw_list"]
    optimization_objective: str = ctx["optimization_objective"]

    route_shap = _score_and_explain_routes(
        routes_schemas=available_routes,
        selected_route=best_route,
        optimization_objective=optimization_objective,
    )

    relay_swaps = [
        {
            "hub_id": st.hub_id,
            "hub_code": st.hub_code,
            "hub_name": st.hub_name,
            "battery_id": st.selected_battery_id,
            "battery_serial": st.selected_battery_serial,
            "battery_charge_pct": st.selected_battery_charge_pct,
            "battery_energy_wh": st.selected_battery_energy_wh,
        }
        for st in (best_route.relay_battery_status or [])
        if st.selected_battery_id
    ]

    exec_summary = (
        f"Auto-dispatch assigned Drone {best_drone.id} ({best_drone.name}) with Battery {best_battery.id} "
        f"({best_battery.charge_level_pct or 0}% SoC) on Route {best_route.routeId} ({best_route.route_string}: "
        f"{best_route.distanceKm:.2f} km, {best_route.predictedEnergyWh:.1f} Wh, {best_route.predictedDurationMin} min, "
        f"{best_route.remainingBatteryPct}% reserve) under {optimization_objective} priority."
    )

    xai_report = AutoDispatchXAIReport(
        delivery_mode=ctx["order_mode_raw"],
        optimization_objective=optimization_objective,
        effective_origin_hub_id=ctx["eff_origin_hub_id"],
        is_replan_dispatch=ctx["is_replan"],
        is_cross_hub_dispatch=bool(best_route.is_cross_hub),
        drone_decision=ctx["drone_shap"],
        battery_decision=ctx["battery_shap"],
        route_decision=route_shap,
        physics_energy_shap=best_route.shap_values,
        physics_energy_explanation=best_route.ai_explanation,
        relay_swap_batteries=relay_swaps,
        executive_summary=exec_summary,
    )

    return {
        "order": order,
        "bundled_order_ids": ctx["clean_bundle_ids"],
        "total_payload_kg": ctx["total_payload_kg"],
        "total_fee": ctx["total_fee"],
        "drone": best_drone,
        "battery": best_battery,
        "selected_route": best_route,
        "available_routes": available_routes,
        "raw_list": raw_list,
        "xai_report": xai_report,
    }
