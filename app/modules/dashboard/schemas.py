from pydantic import BaseModel
from typing import Dict


# ======================================================================
# CUSTOMER
# ======================================================================
class CustomerDashboardResponse(BaseModel):
    active_deliveries: int
    completed_deliveries: int
    cancelled_deliveries: int
    total_deliveries: int
    total_spent: float
    avg_payload_kg: float
    total_distance_m: float
    status_breakdown: Dict[str, int]


# ======================================================================
# OPERATOR
# ======================================================================
class OperatorMissionKPI(BaseModel):
    total: int
    completed: int
    cancelled: int
    rejected: int
    success_rate: float
    avg_distance_m: float
    avg_payload_kg: float
    total_revenue: float


class OperatorEnergyKPI(BaseModel):
    total_estimated_energy_wh: float
    avg_estimated_energy_wh: float
    wh_per_km: float


class OperatorRouteEfficiency(BaseModel):
    total_distance_m: float
    avg_distance_m: float
    avg_predicted_duration_min: float


class OperatorAlerts(BaseModel):
    pending_maintenance_alerts: int
    open_incidents: int
    overdue_approvals: int


class OperatorDashboardResponse(BaseModel):
    mission_kpi: OperatorMissionKPI
    status_breakdown: Dict[str, int]
    active_flights: int
    energy_kpi: OperatorEnergyKPI
    route_efficiency: OperatorRouteEfficiency
    alerts: OperatorAlerts


# ======================================================================
# TECHNICIAN
# ======================================================================
class TechnicianDashboardResponse(BaseModel):
    fleet_status: Dict[str, int]
    total_drones: int
    available_drones: int
    drone_availability_pct: float
    avg_utilization_pct: float
    battery_status: Dict[str, int]
    total_batteries: int
    avg_battery_charge_pct: float
    low_battery_count: int
    pending_maintenance_alerts: int
    work_order_status: Dict[str, int]


# ======================================================================
# ADMIN
# ======================================================================
class AdminRevenue(BaseModel):
    total: float
    today: float
    this_month: float
    paid_orders: int
    pending_payments: float   # Không có module payment -> 0
    refunds: float            # Không có module payment -> 0


class AdminUsers(BaseModel):
    total: int
    by_role: Dict[str, int]
    active: int
    inactive: int


class AdminMissions(BaseModel):
    total: int
    by_status: Dict[str, int]
    success_rate: float


class AdminFleet(BaseModel):
    total_drones: int
    by_status: Dict[str, int]
    avg_utilization_pct: float


class AdminBatteries(BaseModel):
    total: int
    by_status: Dict[str, int]
    avg_charge_pct: float


class AdminAI(BaseModel):
    missions_with_prediction: int
    avg_estimated_energy_wh: float
    risk_breakdown: Dict[str, int]


class AdminAlerts(BaseModel):
    pending_maintenance_alerts: int
    open_incidents: int
    total_audit_logs: int


class AdminDashboardResponse(BaseModel):
    revenue: AdminRevenue
    users: AdminUsers
    missions: AdminMissions
    fleet: AdminFleet
    batteries: AdminBatteries
    ai: AdminAI
    alerts: AdminAlerts
