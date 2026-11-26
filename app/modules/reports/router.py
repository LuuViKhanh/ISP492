"""
[Admin] Reports — Export CSV
Prefix: /admin/reports

Endpoints:
  GET /admin/reports/revenue.csv    — Doanh thu theo khoảng thời gian
  GET /admin/reports/missions.csv   — Danh sách missions
  GET /admin/reports/incidents.csv  — Danh sách incidents
  GET /admin/reports/fleet.csv      — Thống kê drone fleet
"""

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import CurrentUser, RoleChecker
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, MissionStatus, Order, Incident
from app.modules.fleet.models import Drone

router = APIRouter(prefix="/admin/reports", tags=["Admin - Reports"])
allow_admin = RoleChecker([UserRole.ADMIN])

_COMPLETED = [
    MissionStatus.ARRIVAL_COMPLETED,
    MissionStatus.MISSION_COMPLETED,
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    """Chuyển list[dict] thành StreamingResponse CSV."""
    if not rows:
        output = io.StringIO()
        output.write("No data available\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse YYYY-MM-DD string to naive datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


# ── Revenue CSV ───────────────────────────────────────────────────────────────

@router.get(
    "/revenue.csv",
    summary="Export báo cáo doanh thu (CSV)",
    response_class=StreamingResponse,
)
async def export_revenue_csv(
    from_date: Optional[str] = Query(None, description="Từ ngày YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="Đến ngày YYYY-MM-DD"),
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Export doanh thu CSV.**

    Mỗi dòng = 1 mission đã hoàn thành, có: order_id, mission_id, hub route,
    delivery_mode, payload_kg, delivery_fee, completed_at.

    Filter theo `from_date` / `to_date` (YYYY-MM-DD).
    """
    from app.modules.system.models import Hub

    q = (
        select(
            Mission.id.label("mission_id"),
            Mission.order_id,
            Mission.delivery_fee,
            Mission.origin_hub_id,
            Mission.destination_hub_id,
            Mission.payload_weight,
            Mission.arrival_confirmed_at,
            Mission.end_time,
            Order.delivery_mode,
            Order.package_type,
        )
        .join(Order, Order.id == Mission.order_id, isouter=True)
        .where(Mission.status.in_(_COMPLETED))
    )

    dt_from = _parse_date(from_date)
    dt_to   = _parse_date(to_date)
    ref_col = Mission.arrival_confirmed_at

    if dt_from:
        q = q.where(ref_col >= dt_from)
    if dt_to:
        from datetime import timedelta
        q = q.where(ref_col < dt_to + timedelta(days=1))

    q = q.order_by(ref_col.desc().nullslast())
    missions = (await db.execute(q)).all()

    # Lấy hub names
    hub_ids = {r.origin_hub_id for r in missions} | {r.destination_hub_id for r in missions}
    hub_ids.discard(None)
    hub_map: dict[int, str] = {}
    if hub_ids:
        hubs = (await db.execute(select(Hub).where(Hub.id.in_(hub_ids)))).scalars().all()
        hub_map = {h.id: h.name for h in hubs}

    rows = []
    for r in missions:
        completed_at = r.arrival_confirmed_at or r.end_time
        rows.append({
            "mission_id":        r.mission_id,
            "order_id":          r.order_id or "",
            "origin_hub":        hub_map.get(r.origin_hub_id, str(r.origin_hub_id or "")),
            "destination_hub":   hub_map.get(r.destination_hub_id, str(r.destination_hub_id or "")),
            "delivery_mode":     r.delivery_mode.value if r.delivery_mode else "",
            "package_type":      r.package_type or "",
            "payload_kg":        round(float(r.payload_weight or 0), 2),
            "delivery_fee_vnd":  int(r.delivery_fee or 0),
            "completed_at":      completed_at.strftime("%Y-%m-%d %H:%M") if completed_at else "",
        })

    today = datetime.now().strftime("%Y%m%d")
    return _csv_response(rows, f"revenue_{today}.csv")


# ── Missions CSV ──────────────────────────────────────────────────────────────

@router.get(
    "/missions.csv",
    summary="Export danh sách missions (CSV)",
    response_class=StreamingResponse,
)
async def export_missions_csv(
    from_date: Optional[str] = Query(None, description="Từ ngày YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="Đến ngày YYYY-MM-DD"),
    status: Optional[str] = Query(None, description="Filter theo status (Completed, Failed, ...)"),
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Export danh sách missions CSV.**

    Mỗi dòng = 1 mission: id, order_id, drone_id, status, route, distance_km,
    payload_kg, energy_wh, duration_min, departed_at, arrived_at, delivery_fee.

    Filter theo `from_date` / `to_date` / `status`.
    """
    from app.modules.system.models import Hub
    from app.modules.ai_predictions.models import AIPrediction

    q = (
        select(
            Mission.id.label("mission_id"),
            Mission.order_id,
            Mission.drone_id,
            Mission.status,
            Mission.origin_hub_id,
            Mission.destination_hub_id,
            Mission.distance_m,
            Mission.payload_weight,
            Mission.delivery_fee,
            Mission.departed_at,
            Mission.arrived_at,
            Mission.arrival_confirmed_at,
            Mission.end_time,
            Mission.scheduled_time,
        )
    )

    dt_from = _parse_date(from_date)
    dt_to   = _parse_date(to_date)

    if dt_from:
        q = q.where(Mission.scheduled_time >= dt_from)
    if dt_to:
        from datetime import timedelta
        q = q.where(Mission.scheduled_time < dt_to + timedelta(days=1))
    if status:
        q = q.where(Mission.status.cast(type_=None).contains(status))

    q = q.order_by(Mission.scheduled_time.desc().nullslast()).limit(5000)
    missions = (await db.execute(q)).all()

    hub_ids = {r.origin_hub_id for r in missions} | {r.destination_hub_id for r in missions}
    hub_ids.discard(None)
    hub_map: dict[int, str] = {}
    if hub_ids:
        hubs = (await db.execute(select(Hub).where(Hub.id.in_(hub_ids)))).scalars().all()
        hub_map = {h.id: h.name for h in hubs}

    # AI prediction for energy/duration
    m_ids = [r.mission_id for r in missions]
    pred_map: dict[str, dict] = {}
    if m_ids:
        preds = (await db.execute(
            select(AIPrediction).where(AIPrediction.mission_id.in_(m_ids))
        )).scalars().all()
        for p in preds:
            pred_map[str(p.mission_id)] = {
                "energy_wh": round(float(p.estimated_energy_wh or 0), 1),
                "duration_min": round(float(p.estimated_duration_m or 0), 1),
            }

    rows = []
    for r in missions:
        dep = r.departed_at
        arr = r.arrived_at or r.end_time
        duration_min = None
        if dep and arr:
            duration_min = round((arr - dep).total_seconds() / 60, 1)
        pred = pred_map.get(str(r.mission_id), {})
        rows.append({
            "mission_id":        r.mission_id,
            "order_id":          r.order_id or "",
            "drone_id":          r.drone_id or "",
            "status":            r.status.value if hasattr(r.status, "value") else str(r.status or ""),
            "origin_hub":        hub_map.get(r.origin_hub_id, str(r.origin_hub_id or "")),
            "destination_hub":   hub_map.get(r.destination_hub_id, str(r.destination_hub_id or "")),
            "distance_km":       round(float(r.distance_m or 0) / 1000, 2),
            "payload_kg":        round(float(r.payload_weight or 0), 2),
            "delivery_fee_vnd":  int(r.delivery_fee or 0),
            "estimated_energy_wh":    pred.get("energy_wh", ""),
            "estimated_duration_min": pred.get("duration_min", ""),
            "actual_duration_min":    duration_min or "",
            "scheduled_at":      r.scheduled_time.strftime("%Y-%m-%d %H:%M") if r.scheduled_time else "",
            "departed_at":       dep.strftime("%Y-%m-%d %H:%M") if dep else "",
            "arrived_at":        arr.strftime("%Y-%m-%d %H:%M") if arr else "",
        })

    today = datetime.now().strftime("%Y%m%d")
    return _csv_response(rows, f"missions_{today}.csv")


# ── Incidents CSV ─────────────────────────────────────────────────────────────

@router.get(
    "/incidents.csv",
    summary="Export báo cáo incidents (CSV)",
    response_class=StreamingResponse,
)
async def export_incidents_csv(
    from_date: Optional[str] = Query(None, description="Từ ngày YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="Đến ngày YYYY-MM-DD"),
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Export danh sách incidents CSV.**

    Mỗi dòng = 1 incident: id, mission_id, drone_id, severity, status,
    description, compensation_pct, requires_inspection, reported_at.
    """
    from app.modules.missions.models import IncidentStatus

    q = select(Incident).order_by(Incident.reported_at.desc().nullslast())

    dt_from = _parse_date(from_date)
    dt_to   = _parse_date(to_date)
    if dt_from:
        q = q.where(Incident.reported_at >= dt_from)
    if dt_to:
        from datetime import timedelta
        q = q.where(Incident.reported_at < dt_to + timedelta(days=1))

    q = q.limit(5000)
    incidents = (await db.execute(q)).scalars().all()

    rows = []
    for inc in incidents:
        rows.append({
            "incident_id":               inc.id,
            "mission_id":                inc.mission_id or "",
            "drone_id":                  inc.drone_id or "",
            "severity":                  inc.severity.value if hasattr(inc.severity, "value") else str(inc.severity or ""),
            "status":                    inc.status.value if hasattr(inc.status, "value") else str(inc.status or ""),
            "description":               (inc.description or "").replace("\n", " "),
            "requires_technical_inspection": "Yes" if inc.requires_technical_inspection else "No",
            "compensation_pct":          inc.compensation_pct or "",
            "compensation_note":         (inc.compensation_note or "").replace("\n", " "),
            "reported_at":               inc.reported_at.strftime("%Y-%m-%d %H:%M") if inc.reported_at else "",
        })

    today = datetime.now().strftime("%Y%m%d")
    return _csv_response(rows, f"incidents_{today}.csv")


# ── Drone Fleet CSV ───────────────────────────────────────────────────────────

@router.get(
    "/fleet.csv",
    summary="Export thống kê drone fleet (CSV)",
    response_class=StreamingResponse,
)
async def export_fleet_csv(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Export thống kê toàn bộ drone fleet CSV.**

    Mỗi dòng = 1 drone: id, name, model, serial, hub, status, flight_hours,
    total_missions, last_maintenance_at, utilization_pct.
    """
    from sqlalchemy import func
    from app.modules.system.models import Hub

    # Count missions per drone
    mission_counts = (await db.execute(
        select(Mission.drone_id, func.count(Mission.id).label("total"))
        .where(Mission.drone_id.isnot(None))
        .group_by(Mission.drone_id)
    )).all()
    mission_count_map = {r.drone_id: r.total for r in mission_counts}

    drones = (await db.execute(select(Drone).order_by(Drone.name))).scalars().all()

    hub_ids = {d.current_hub_id for d in drones if d.current_hub_id}
    hub_map: dict[int, str] = {}
    if hub_ids:
        hubs = (await db.execute(select(Hub).where(Hub.id.in_(hub_ids)))).scalars().all()
        hub_map = {h.id: h.name for h in hubs}

    rows = []
    for d in drones:
        rows.append({
            "drone_id":           d.id,
            "name":               d.name,
            "model":              d.model,
            "serial_number":      d.serial_number,
            "current_hub":        hub_map.get(d.current_hub_id, str(d.current_hub_id or "Unassigned")),
            "status":             d.status.value if hasattr(d.status, "value") else str(d.status or ""),
            "total_flight_hours": round(float(d.total_flight_hours or 0), 2),
            "total_missions":     mission_count_map.get(d.id, 0),
            "utilization_pct":    round(float(d.utilization_pct or 0), 1),
            "battery_level_pct":  d.battery_level_pct or "",
            "last_maintenance_at": d.last_maintenance_at.strftime("%Y-%m-%d") if d.last_maintenance_at else "Never",
        })

    today = datetime.now().strftime("%Y%m%d")
    return _csv_response(rows, f"fleet_{today}.csv")


# ── Cancellation Stats ────────────────────────────────────────────────────────

@router.get(
    "/cancellation-stats",
    summary="Thống kê lý do hủy đơn hàng",
)
async def get_cancellation_stats(
    from_date: Optional[str] = Query(None, description="Từ ngày YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="Đến ngày YYYY-MM-DD"),
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Thống kê lý do hủy đơn hàng.**

    Trả về: tổng đơn hủy, phân phối theo lý do, top 5 lý do phổ biến nhất.
    """
    from sqlalchemy import func
    from app.modules.missions.models import OrderStatus as _OS

    q = (
        select(
            Order.cancellation_reason,
            func.count(Order.id).label("count"),
        )
        .where(Order.status == _OS.CANCELLED)
        .group_by(Order.cancellation_reason)
        .order_by(func.count(Order.id).desc())
    )

    dt_from = _parse_date(from_date)
    dt_to   = _parse_date(to_date)
    if dt_from:
        q = q.where(Order.cancelled_at >= dt_from)
    if dt_to:
        from datetime import timedelta
        q = q.where(Order.cancelled_at < dt_to + timedelta(days=1))

    rows = (await db.execute(q)).all()

    total = sum(r.count for r in rows)
    distribution = [
        {
            "reason": r.cancellation_reason or "not_specified",
            "count": r.count,
            "pct": round(r.count / total * 100, 1) if total else 0.0,
        }
        for r in rows
    ]

    return {
        "total_cancelled": total,
        "distribution": distribution,
        "top_reason": distribution[0]["reason"] if distribution else None,
    }


# ── Dashboard Chart Data ──────────────────────────────────────────────────────

@router.get(
    "/chart/revenue",
    summary="Dữ liệu biểu đồ doanh thu theo ngày (30 ngày gần nhất)",
)
async def get_revenue_chart(
    days: int = 30,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Dữ liệu doanh thu theo ngày cho biểu đồ FE.**

    Trả về mảng `{date, revenue, mission_count}` cho `days` ngày gần nhất (mặc định 30).
    FE dùng để vẽ line/bar chart doanh thu.
    """
    from sqlalchemy import func, cast, Date as SADate
    from datetime import timedelta

    days = max(7, min(days, 365))
    cutoff = datetime.now() - timedelta(days=days)

    rows = (await db.execute(
        select(
            cast(Mission.arrival_confirmed_at, SADate).label("day"),
            func.coalesce(func.sum(Mission.delivery_fee), 0.0).label("revenue"),
            func.count(Mission.id).label("mission_count"),
        )
        .where(
            Mission.status.in_(_COMPLETED),
            Mission.arrival_confirmed_at >= cutoff,
        )
        .group_by(cast(Mission.arrival_confirmed_at, SADate))
        .order_by(cast(Mission.arrival_confirmed_at, SADate))
    )).all()

    return {
        "days": days,
        "data": [
            {
                "date": str(r.day),
                "revenue": int(r.revenue or 0),
                "mission_count": r.mission_count,
            }
            for r in rows
        ],
    }


@router.get(
    "/chart/missions",
    summary="Dữ liệu biểu đồ missions theo ngày (30 ngày gần nhất)",
)
async def get_missions_chart(
    days: int = 30,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Phân phối missions theo status theo ngày.**

    Trả về mảng `{date, completed, failed, cancelled}` cho `days` ngày gần nhất.
    """
    from sqlalchemy import func, cast, Date as SADate, case
    from datetime import timedelta

    days = max(7, min(days, 365))
    cutoff = datetime.now() - timedelta(days=days)

    rows = (await db.execute(
        select(
            cast(Mission.scheduled_time, SADate).label("day"),
            func.count(Mission.id).label("total"),
            func.count(case((Mission.status.in_(_COMPLETED), 1))).label("completed"),
            func.count(case((Mission.status == MissionStatus.FAILED, 1))).label("failed"),
            func.count(case((Mission.status == MissionStatus.CANCELLED, 1))).label("cancelled"),
        )
        .where(Mission.scheduled_time >= cutoff)
        .group_by(cast(Mission.scheduled_time, SADate))
        .order_by(cast(Mission.scheduled_time, SADate))
    )).all()

    return {
        "days": days,
        "data": [
            {
                "date": str(r.day),
                "total": r.total,
                "completed": r.completed,
                "failed": r.failed,
                "cancelled": r.cancelled,
            }
            for r in rows
        ],
    }
