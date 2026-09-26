"""
target_calculation.py
=====================
Shared target and wind-geometry calculations for DJI + VTOL.

Scientific conventions
----------------------
1. Distance and battery energy use the SAME temporal support rule when timestamps
   are provided: only adjacent samples with 0 < dt <= max_gap_sec are integrated.
2. Energy uses trapezoidal integration on real timestamps.
3. Weather wind direction is treated as meteorological FROM direction.
4. Relative wind angle compares route bearing (direction of travel) directly
   with meteorological wind FROM direction:
      0 deg   = headwind
      90 deg  = crosswind
      180 deg = tailwind
5. headwind_component is POSITIVE for headwind and NEGATIVE for tailwind.
6. Route bearing is an intermediate geometry value, not a model feature.
"""

import numpy as np

EARTH_RADIUS_M = 6_371_000.0
DEFAULT_MAX_GAP_SEC = 30.0


def _as_float_array(values):
    return np.asarray(values, dtype=float)


def build_valid_segment_mask(times, *endpoint_arrays, max_gap_sec=DEFAULT_MAX_GAP_SEC):
    """Return a boolean mask for adjacent, temporally valid sample pairs.

    A segment i connects sample i -> i+1 and is valid when:
      - both timestamps are finite,
      - dt_i = t[i+1] - t[i] is strictly positive,
      - dt_i <= max_gap_sec (if max_gap_sec is not None),
      - every supplied endpoint array is finite at BOTH endpoints.

    The mask length is len(times)-1.
    """
    times = _as_float_array(times)
    if len(times) < 2:
        return np.zeros(0, dtype=bool)

    dt = np.diff(times)
    mask = np.isfinite(times[:-1]) & np.isfinite(times[1:]) & (dt > 0)
    if max_gap_sec is not None:
        mask &= dt <= float(max_gap_sec)

    for arr in endpoint_arrays:
        arr = _as_float_array(arr)
        if len(arr) != len(times):
            raise ValueError("All endpoint arrays must have the same length as times")
        mask &= np.isfinite(arr[:-1]) & np.isfinite(arr[1:])

    return mask


def calculate_flight_duration(times):
    """Calculate observed flight duration as max(time) - min(time), in seconds."""
    times = _as_float_array(times)
    times = times[np.isfinite(times)]
    if len(times) < 2:
        return 0.0
    return float(times.max() - times.min())


def calculate_battery_energy(
    voltages,
    currents,
    times,
    normalize_sign=False,
    max_gap_sec=DEFAULT_MAX_GAP_SEC,
):
    """Calculate observed battery energy using trapezoidal integration.

    Parameters
    ----------
    voltages : array-like
        Battery voltage, V.
    currents : array-like
        Battery current, A.
    times : array-like
        Timestamp in seconds.
    normalize_sign : bool
        If True, use abs(current). This is intended only for a platform whose
        discharge-current convention is predominantly negative. Do NOT use it
        merely because a small fraction of samples are negative.
    max_gap_sec : float or None
        Maximum telemetry gap bridged by integration. The default (30 s) is
        intentionally identical to the distance functions. Set None only for a
        diagnostic reproducing legacy behavior.

    Formula
    -------
        P_i = V_i * I_i
        dt_i = t[i+1] - t[i]
        E_Wh = sum_valid( ((P_i + P_{i+1})/2) * dt_i ) / 3600

    Returns
    -------
    float
        Observed energy over valid telemetry-supported segments, Wh.
    """
    voltages = _as_float_array(voltages)
    currents = _as_float_array(currents)
    times = _as_float_array(times)

    if not (len(voltages) == len(currents) == len(times)):
        raise ValueError("voltages, currents, and times must have the same length")
    if len(times) < 2:
        return 0.0

    if normalize_sign:
        currents = np.abs(currents)

    power = voltages * currents
    dt = np.diff(times)
    avg_power = (power[:-1] + power[1:]) / 2.0
    valid = build_valid_segment_mask(
        times, voltages, currents, max_gap_sec=max_gap_sec
    )

    if not np.any(valid):
        return 0.0

    energy_joules = np.sum(avg_power[valid] * dt[valid])
    return float(energy_joules / 3600.0)


def calculate_distance_haversine(lats, lons, alts, times=None, max_gap_sec=DEFAULT_MAX_GAP_SEC):
    """Calculate cumulative 3D geographic distance, in meters.

    Adjacent samples are integrated only when both endpoints are finite and, if
    timestamps are provided, 0 < dt <= max_gap_sec. Geographic (0, 0) samples
    are treated as invalid for these study sites.
    """
    lats = _as_float_array(lats)
    lons = _as_float_array(lons)
    alts = _as_float_array(alts)

    if not (len(lats) == len(lons) == len(alts)):
        raise ValueError("lats, lons, and alts must have the same length")
    if len(lats) < 2:
        return 0.0

    endpoint_valid = (
        np.isfinite(lats)
        & np.isfinite(lons)
        & np.isfinite(alts)
        & ~((lats == 0.0) & (lons == 0.0))
    )

    lat_rad = np.radians(lats)
    lon_rad = np.radians(lons)
    dlat = np.diff(lat_rad)
    dlon = np.diff(lon_rad)
    dalt = np.diff(alts)

    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat_rad[:-1]) * np.cos(lat_rad[1:]) * np.sin(dlon / 2.0) ** 2
    )
    # Numerical guard against tiny floating-point excursions outside [0, 1].
    a = np.clip(a, 0.0, 1.0)
    c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    horizontal = EARTH_RADIUS_M * c
    segment_distance = np.sqrt(horizontal ** 2 + dalt ** 2)

    valid = endpoint_valid[:-1] & endpoint_valid[1:] & np.isfinite(segment_distance)
    if times is not None:
        times = _as_float_array(times)
        if len(times) != len(lats):
            raise ValueError("times must have the same length as coordinate arrays")
        valid &= build_valid_segment_mask(
            times, lats, lons, alts, max_gap_sec=max_gap_sec
        )

    return float(np.sum(segment_distance[valid]))


def calculate_distance_euclidean(xs, ys, zs, times=None, max_gap_sec=DEFAULT_MAX_GAP_SEC):
    """Calculate cumulative 3D Euclidean distance for local XYZ coordinates."""
    xs = _as_float_array(xs)
    ys = _as_float_array(ys)
    zs = _as_float_array(zs)

    if not (len(xs) == len(ys) == len(zs)):
        raise ValueError("xs, ys, and zs must have the same length")
    if len(xs) < 2:
        return 0.0

    dx = np.diff(xs)
    dy = np.diff(ys)
    dz = np.diff(zs)
    segment_distance = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
    valid = (
        np.isfinite(xs[:-1]) & np.isfinite(xs[1:])
        & np.isfinite(ys[:-1]) & np.isfinite(ys[1:])
        & np.isfinite(zs[:-1]) & np.isfinite(zs[1:])
        & np.isfinite(segment_distance)
    )

    if times is not None:
        times = _as_float_array(times)
        if len(times) != len(xs):
            raise ValueError("times must have the same length as coordinate arrays")
        valid &= build_valid_segment_mask(
            times, xs, ys, zs, max_gap_sec=max_gap_sec
        )

    return float(np.sum(segment_distance[valid]))


def calculate_energy_efficiency(distance, battery_consumed_wh):
    """Energy efficiency EE = distance / energy, in m/Wh."""
    if (
        np.isfinite(distance)
        and np.isfinite(battery_consumed_wh)
        and distance >= 0
        and battery_consumed_wh > 0
    ):
        return float(distance / battery_consumed_wh)
    return float("nan")


def circular_mean(angles_deg):
    """Circular mean of angles in degrees, returned in [0, 360)."""
    angles = _as_float_array(angles_deg)
    angles = angles[np.isfinite(angles)]
    if len(angles) == 0:
        return float("nan")
    angles_rad = np.radians(angles)
    mean_rad = np.arctan2(np.mean(np.sin(angles_rad)), np.mean(np.cos(angles_rad)))
    return float(np.degrees(mean_rad) % 360.0)


def _initial_bearing_geographic(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise from North."""
    lat1 = np.radians(float(lat1_deg))
    lat2 = np.radians(float(lat2_deg))
    dlon = np.radians(float(lon2_deg) - float(lon1_deg))

    y = np.sin(dlon) * np.cos(lat2)
    x = (
        np.cos(lat1) * np.sin(lat2)
        - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    )
    return float((np.degrees(np.arctan2(y, x)) + 360.0) % 360.0)


def compute_route_direction(start_x, start_y, end_x, end_y, coordinate_mode="auto"):
    """Compute route direction in degrees clockwise from North.

    For GEOGRAPHIC coordinates, x=longitude and y=latitude.
    For LOCAL_XYZ, x is East/West and y is North/South, so atan2(dx, dy)
    yields 0=N, 90=E.
    """
    vals = np.asarray([start_x, start_y, end_x, end_y], dtype=float)
    if not np.all(np.isfinite(vals)):
        return float("nan")

    sx, sy, ex, ey = vals
    # Do not use np.isclose default relative tolerance on longitude/latitude;
    # at values near -80 deg it can incorrectly treat meter-scale displacement
    # as identical. Only exact/nearly machine-zero displacement is undefined.
    if abs(ex - sx) < 1e-12 and abs(ey - sy) < 1e-12:
        return float("nan")

    mode = str(coordinate_mode).upper()
    if mode == "AUTO":
        looks_geographic = (
            -180 <= sx <= 180 and -180 <= ex <= 180
            and -90 <= sy <= 90 and -90 <= ey <= 90
        )
        mode = "GEOGRAPHIC" if looks_geographic else "LOCAL_XYZ"

    if mode == "GEOGRAPHIC":
        return _initial_bearing_geographic(sy, sx, ey, ex)
    if mode == "LOCAL_XYZ":
        dx = ex - sx
        dy = ey - sy
        return float((np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0)

    raise ValueError("coordinate_mode must be 'auto', 'GEOGRAPHIC', or 'LOCAL_XYZ'")


def compute_relative_wind_features(
    start_x,
    start_y,
    end_x,
    end_y,
    wind_dir_from_deg,
    wind_speed_ms,
    coordinate_mode="auto",
):
    """Compute route-known wind interaction features.

    Parameters
    ----------
    start_x, start_y, end_x, end_y
        Known route/edge geometry. For GEOGRAPHIC mode x=longitude, y=latitude.
    wind_dir_from_deg : float
        Meteorological wind direction in degrees clockwise from North. This is
        the direction the wind COMES FROM.
    wind_speed_ms : float
        Wind speed in m/s.

    Convention
    ----------
    Let B be route bearing (direction the UAV travels toward) and W be the
    meteorological wind FROM direction:

        delta = |B - W| mod 360
        relative_wind_angle = min(delta, 360 - delta)

    Therefore:
        0 deg   = headwind
        90 deg  = crosswind
        180 deg = tailwind

    The route bearing is used only as an intermediate geometric quantity; it is
    deliberately not returned as a model feature.

    Components
    ----------
        headwind_component = wind_speed * cos(relative_angle)
        crosswind_component = |wind_speed * sin(relative_angle)|

    Positive headwind_component means opposing airflow; negative means tailwind.

    Returns
    -------
    relative_wind_angle : float
        Angle in [0, 180] degrees, with 0=headwind and 180=tailwind.
    headwind_component : float
        m/s, positive=headwind and negative=tailwind.
    crosswind_component : float
        m/s, non-negative crosswind magnitude.
    """
    values = np.asarray([wind_dir_from_deg, wind_speed_ms], dtype=float)
    if not np.all(np.isfinite(values)):
        return float("nan"), float("nan"), float("nan")

    route_dir = compute_route_direction(
        start_x, start_y, end_x, end_y, coordinate_mode=coordinate_mode
    )
    if not np.isfinite(route_dir):
        return float("nan"), float("nan"), float("nan")

    delta = abs(float(route_dir) - float(wind_dir_from_deg)) % 360.0
    relative = min(delta, 360.0 - delta)

    theta = np.radians(relative)
    speed = max(float(wind_speed_ms), 0.0)
    headwind = speed * np.cos(theta)  # + headwind, - tailwind
    crosswind = abs(speed * np.sin(theta))

    return float(relative), float(headwind), float(crosswind)


def compute_planned_wind_features(
    start_x,
    start_y,
    end_x,
    end_y,
    wind_dir_from_deg,
    wind_speed_ms,
    coordinate_mode="auto",
):
    """Backward-compatible alias for route-known wind features.

    Historical code uses the ``planned_*`` column names. The mathematical
    convention is now canonical and identical to ``compute_relative_wind_features``:
    0=headwind, 90=crosswind, 180=tailwind.
    """
    return compute_relative_wind_features(
        start_x,
        start_y,
        end_x,
        end_y,
        wind_dir_from_deg,
        wind_speed_ms,
        coordinate_mode=coordinate_mode,
    )

def compute_relative_wind_angle(flight_data):
    """Compute the legacy OBSERVED relative wind angle from actual trajectory.

    This function is kept for historical analysis only. It derives headings from
    measured position_x/position_y and therefore must NOT be treated as a clean
    pre-flight input. The semantic convention of the onboard ``wind_angle``
    channel should also be documented per dataset before physical interpretation.
    """
    if "wind_angle" not in flight_data.columns:
        return float("nan")

    x = _as_float_array(flight_data["position_x"].values)
    y = _as_float_array(flight_data["position_y"].values)
    wind_dirs = _as_float_array(flight_data["wind_angle"].values[1:])
    dx = np.diff(x)
    dy = np.diff(y)
    headings = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0

    rel_angles = np.abs(headings - wind_dirs) % 360.0
    rel_angles = np.where(rel_angles > 180.0, 360.0 - rel_angles, rel_angles)

    movement = np.sqrt(dx ** 2 + dy ** 2)
    mask = (
        movement > 1e-8
    ) & np.isfinite(headings) & np.isfinite(wind_dirs) & np.isfinite(rel_angles)

    if np.any(mask):
        return float(np.mean(rel_angles[mask]))
    return float("nan")
