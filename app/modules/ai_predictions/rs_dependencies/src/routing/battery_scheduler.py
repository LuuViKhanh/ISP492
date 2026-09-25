"""
battery_scheduler.py
====================
Core dispatch logic for Battery Scheduling.
Enforces explicit semantics for safety margins and reserves.
"""

def evaluate_dispatch_safety(soc: float, capacity_wh: float, required_energy_wh: float, min_reserve_fraction: float) -> bool:
    """
    Evaluates if a drone can safely be dispatched.
    
    soc: State of Charge (0.0 to 1.0)
    capacity_wh: Total battery capacity in Wh (e.g. 99.9)
    required_energy_wh: Energy predicted/allocated for the flight
    min_reserve_fraction: Fraction of capacity that MUST be kept in reserve (e.g. 0.20 for 20%)
    """
    available_energy_wh = soc * capacity_wh
    minimum_reserve_wh = capacity_wh * min_reserve_fraction
    
    # Explicit dispatch rule
    safe = (available_energy_wh - required_energy_wh >= minimum_reserve_wh)
    
    return safe

def get_required_energy_for_scheduler(scheduler_type: str, point_pred: float, upper_bound: float) -> float:
    """
    Determines what energy value to use for dispatch based on scheduler type.
    S0 (Point): Uses point prediction
    S1 (UCB): Uses upper confidence bound (conservative)
    S2 (OOD-aware UCB): Handled at the pipeline level (rejects outright if OOD is high, else acts like S1).
    """
    if scheduler_type == "S0_POINT":
        return point_pred
    elif scheduler_type in ["S1_UCB", "S2_OOD_UCB"]:
        return upper_bound
    else:
        raise ValueError(f"Unknown scheduler type: {scheduler_type}")
