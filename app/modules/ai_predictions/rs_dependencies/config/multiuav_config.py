"""
Central configuration for Multi-UAV validation and Multi-Hub energy analysis.

Research design
===============
The repository uses three independent validation cases:

1. DJI_ONLY
   DJI data is processed and evaluated independently.
   It MUST NOT pass through the DJI+VTOL combine step.

2. VTOL_ONLY
   VTOL data is processed and evaluated independently.
   It MUST NOT pass through the DJI+VTOL combine step.

3. COMBINED
   Clean DJI and VTOL flight-level datasets are harmonized and combined
   explicitly before model validation.

These three cases are validation experiments used to select a defensible
data/model configuration. They are not the final research output.

Targets
=======
Flight-level primary target:
    energy_efficiency = distance / battery_consumed_wh  [m/Wh]

Flight-level secondary target:
    battery_consumed_wh                                [Wh]

Multi-Hub segment target:
    segment_energy_wh                                  [Wh]

Energy is used at segment/edge level because route energy is additive:
    E_route = sum(E_segment)

Route-level energy efficiency is calculated AFTER energy aggregation:
    EE_route = route_distance / E_route

Distance policy
===============
Variables keep concise names such as:
    distance
    segment_distance
    route_distance

However, distance must be computed from cumulative 3-D point-to-point motion:
    horizontal_distance = Haversine(lat/lon)
    delta_h = altitude_2 - altitude_1
    point_distance = sqrt(horizontal_distance^2 + delta_h^2)

The implementation of this geometry belongs in a geometry utility, not in
this configuration module.

Feature policy
==============
Primary flight-level EE validation uses only scientifically valid,
pre-flight/deployable operational and environmental features.

Actual/post-flight quantities such as observed flight duration, actual flight
distance, observed trajectory geometry, battery energy, and historical
trajectory-derived wind angle are excluded from the primary EE predictor.

relative_wind_angle
-------------------
At Multi-Hub segment/edge level, route geometry is known. A route bearing may
therefore be calculated as an INTERMEDIATE geometric quantity and combined
with forecast wind direction to derive relative_wind_angle.

route_bearing itself is NOT a model feature.

For meteorological wind direction ("wind FROM" direction), the convention is:
    0 deg   = headwind
    90 deg  = crosswind
    180 deg = tailwind

Human decision gates
====================
No approved:true/false YAML gate is defined here.

Research decisions are made at explicit command checkpoints:
    CP1_FEATURE_SET_SELECTION
    CP2_MODEL_SELECTION
    CP3_VALIDATED_CONFIGURATION_SELECTION

Each preceding stage should produce evidence and stop. The next command
records the selected option and continues.
"""

from __future__ import annotations

from typing import Dict, List


# ---------------------------------------------------------------------------
# Dataset / platform definitions
# ---------------------------------------------------------------------------

CASE_DJI_ONLY = "DJI_ONLY"
CASE_VTOL_ONLY = "VTOL_ONLY"
CASE_COMBINED = "COMBINED"

VALIDATION_CASES = (
    CASE_DJI_ONLY,
    CASE_VTOL_ONLY,
    CASE_COMBINED,
)

SITE_CONFIG = {
    "DJI": {
        "name": "DJI Matrice 100",
        "site": "Penn Hills, Pennsylvania, USA",
        "lat": 40.465690,
        "lon": -79.788281,
        "timezone": "America/New_York",
        "dataset_id": "DJI_M100",
        "uav_type": "QUADCOPTER",
    },
    "VTOL": {
        "name": "MakeFlyEasy Fighter VTOL",
        "site": "Nardo Flight Test Field, Allison Park, Pennsylvania, USA",
        "faa_id": "77PA",
        "lat": 40.5834006,
        "lon": -79.8997747,
        "timezone": "America/New_York",
        "dataset_id": "VTOL",
        "uav_type": "VTOL_FIXED_WING",
    },
}

TIMEZONE = {
    "DJI": "America/New_York",
    "VTOL": "America/New_York",
}


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

# Primary target for DJI_ONLY / VTOL_ONLY / COMBINED validation.
PRIMARY_TARGET = "energy_efficiency"

# Secondary target for battery-consumption analysis (RQ support).
SECONDARY_TARGET = "battery_consumed_wh"

# Target for segment-level Multi-Hub routing model.
SEGMENT_TARGET = "segment_energy_wh"

TARGET_UNITS = {
    PRIMARY_TARGET: "m/Wh",
    SECONDARY_TARGET: "Wh",
    SEGMENT_TARGET: "Wh",
}


# ---------------------------------------------------------------------------
# Scientifically valid feature groups
# ---------------------------------------------------------------------------

# Operational variables available for a planned/defined mission condition.
OPERATIONAL_FEATURES = [
    "speed",
    "altitude",
    "payload",
]

# Environmental variables obtained from weather observations/forecasts.
# relative_wind_angle is intentionally NOT included here at flight level:
# it is only cleanly defined when route/edge direction is known.
ENVIRONMENTAL_FEATURES = [
    "temperature_c",
    "dew_point_c",
    "humidity_pct",
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_dir_deg",
    "precipitation_mm",
    "pressure_hpa",
]

# Clean common pre-flight baseline used by current Multi-UAV code.
BASE_PREFLIGHT_FEATURES = (
    OPERATIONAL_FEATURES + ENVIRONMENTAL_FEATURES
)

# Backward-compatible name used throughout the existing repository.
COMMON_PREFLIGHT_FEATURES = BASE_PREFLIGHT_FEATURES.copy()

# Platform identity is only a candidate feature in COMBINED validation.
PLATFORM_FEATURES = [
    "is_vtol",
]

# Multi-Hub geometry-derived feature.
# route_bearing is only an intermediate variable and must not be added here.
SEGMENT_AERO_FEATURES = [
    "relative_wind_angle",
]

# Segment distance is known from the candidate edge geometry and is valid for
# the segment Energy model. The variable name remains "segment_distance".
SEGMENT_ROUTE_FEATURES = [
    "segment_distance",
]

SEGMENT_ENERGY_FEATURES = (
    BASE_PREFLIGHT_FEATURES
    + SEGMENT_AERO_FEATURES
    + SEGMENT_ROUTE_FEATURES
)


# ---------------------------------------------------------------------------
# Canonical feature sets for checkpoint-based validation
# ---------------------------------------------------------------------------

FS_OPS = "FS_OPS"
FS_OPS_ENV = "FS_OPS_ENV"
FS_OPS_PLATFORM = "FS_OPS_PLATFORM"
FS_OPS_ENV_PLATFORM = "FS_OPS_ENV_PLATFORM"

VALIDATION_FEATURE_SETS: Dict[str, List[str]] = {
    FS_OPS: OPERATIONAL_FEATURES,
    FS_OPS_ENV: BASE_PREFLIGHT_FEATURES,
    # Platform-controlled operational baseline for COMBINED only.
    # This isolates the contribution of UAV identity from weather.
    FS_OPS_PLATFORM: OPERATIONAL_FEATURES + PLATFORM_FEATURES,
    FS_OPS_ENV_PLATFORM: BASE_PREFLIGHT_FEATURES + PLATFORM_FEATURES,
}

# Which feature sets are scientifically eligible for each validation case.
CASE_FEATURE_SET_OPTIONS = {
    CASE_DJI_ONLY: (
        FS_OPS,
        FS_OPS_ENV,
    ),
    CASE_VTOL_ONLY: (
        FS_OPS,
        FS_OPS_ENV,
    ),
    CASE_COMBINED: (
        FS_OPS,
        FS_OPS_ENV,
        FS_OPS_PLATFORM,
        FS_OPS_ENV_PLATFORM,
    ),
}

# Recommended starting baseline, NOT an automatically approved winner.
DEFAULT_VALIDATION_FEATURE_SET = FS_OPS_ENV


def get_validation_feature_set(
    feature_set: str,
    case: str | None = None,
) -> List[str]:
    """
    Return a copy of a canonical validation feature set.

    If `case` is supplied, enforce that the feature set is scientifically
    eligible for that validation case. In particular, is_vtol is not permitted
    in DJI_ONLY or VTOL_ONLY.
    """
    if feature_set not in VALIDATION_FEATURE_SETS:
        valid = ", ".join(VALIDATION_FEATURE_SETS)
        raise KeyError(
            f"Unknown validation feature set '{feature_set}'. "
            f"Valid sets: {valid}"
        )

    if case is not None:
        if case not in VALIDATION_CASES:
            raise KeyError(
                f"Unknown validation case '{case}'. "
                f"Valid cases: {', '.join(VALIDATION_CASES)}"
            )
        allowed = CASE_FEATURE_SET_OPTIONS[case]
        if feature_set not in allowed:
            raise ValueError(
                f"Feature set '{feature_set}' is not valid for case '{case}'. "
                f"Allowed: {', '.join(allowed)}"
            )

    return list(VALIDATION_FEATURE_SETS[feature_set])


def get_case_feature_set_options(case: str) -> List[str]:
    """Return the feature-set options to benchmark at CP1 for one case."""
    if case not in VALIDATION_CASES:
        raise KeyError(
            f"Unknown validation case '{case}'. "
            f"Valid cases: {', '.join(VALIDATION_CASES)}"
        )
    return list(CASE_FEATURE_SET_OPTIONS[case])


def get_segment_energy_features() -> List[str]:
    """
    Return the feature list for segment-level Multi-Hub Energy prediction.

    Current policy:
        operational
        + environmental
        + relative_wind_angle
        + segment_distance

    `route_bearing` is deliberately absent because it is only used as an
    intermediate geometric value to derive relative_wind_angle.
    """
    return list(SEGMENT_ENERGY_FEATURES)


# ---------------------------------------------------------------------------
# Legacy / extended-research compatibility
# ---------------------------------------------------------------------------
#
# Existing D07-D13 code currently imports FEATURE_SETS, DEFAULT_FEATURE_SET and
# get_feature_set(). Keep those symbols during the refactor so the repository
# can be migrated file-by-file rather than broken all at once.
#
# F0_BASE maps to the clean operational + environmental baseline.
#
# The planned aero proxy sets remain available only for the existing extended
# experiments. They are NOT part of the canonical three-case validation plan.

ROUTE_PROXY_AERO_FEATURES = [
    "planned_relative_wind_angle",
    "planned_headwind_component",
    "planned_crosswind_component",
]

ROUTE_PROXY_METADATA_COLUMNS = [
    "route_endpoint_displacement_m",
    "route_endpoint_to_path_ratio",
    "route_geometry_source",
]

AERO_FEATURE_SETS_REQUIRING_PLANNED_GEOMETRY = {
    "F1_ANGLE",
    "F2_COMPONENTS",
    "F3_ALL_AERO",
}

FEATURE_SETS = {
    # Clean backward-compatible baseline.
    "F0_BASE": BASE_PREFLIGHT_FEATURES,

    # Extended/supporting experiments only.
    "F1_ANGLE": BASE_PREFLIGHT_FEATURES
    + ["planned_relative_wind_angle"],
    "F2_COMPONENTS": BASE_PREFLIGHT_FEATURES
    + [
        "planned_headwind_component",
        "planned_crosswind_component",
    ],
    "F3_ALL_AERO": BASE_PREFLIGHT_FEATURES
    + ROUTE_PROXY_AERO_FEATURES,
}

DEFAULT_FEATURE_SET = "F0_BASE"


def get_feature_set(name: str | None = None) -> List[str]:
    """
    Backward-compatible feature-set loader for existing D07-D13 scripts.

    New validation code should prefer get_validation_feature_set().
    """
    key = name or DEFAULT_FEATURE_SET
    if key not in FEATURE_SETS:
        raise KeyError(
            f"Unknown legacy feature set '{key}'. "
            f"Valid: {list(FEATURE_SETS)}"
        )
    return list(FEATURE_SETS[key])


# ---------------------------------------------------------------------------
# Columns that must NOT silently enter the primary EE predictor
# ---------------------------------------------------------------------------

METADATA_COLUMNS = [
    "flight_uid",
    "flight",
    "route",
    "date",
    "dataset_id",
    "uav_type",
]

# Analysis/target-component columns. These may be used for retrospective
# diagnostics or target construction, but not as inputs to the primary
# pre-flight EE model.
POST_FLIGHT_FEATURES = [
    "flight_duration",
    "distance",
    "avg_wind_speed",
    "relative_wind_angle",
]

TARGET_COMPONENT_COLUMNS = [
    "distance",
    SECONDARY_TARGET,
    PRIMARY_TARGET,
]

FORBIDDEN_PRIMARY_EE_INPUTS = sorted(
    set(POST_FLIGHT_FEATURES + TARGET_COMPONENT_COLUMNS)
)


def validate_primary_ee_feature_list(features: List[str]) -> None:
    """
    Fail fast if a primary flight-level EE feature list contains leakage or
    post-flight variables.

    This check is intentionally strict. It protects the core validation
    pipeline from accidentally re-introducing actual distance, observed flight
    duration, battery energy, or historical relative-wind angle.
    """
    bad = sorted(set(features) & set(FORBIDDEN_PRIMARY_EE_INPUTS))
    if bad:
        raise ValueError(
            "Primary EE feature list contains forbidden post-flight/target "
            f"variables: {bad}"
        )


# ---------------------------------------------------------------------------
# Model benchmark candidates
# ---------------------------------------------------------------------------

# Current short list used by existing extended experiments.
SHORTLIST_MODELS = [
    "LinearRegression",
    "ExtraTrees",
    "XGBoost",
    "CatBoost",
]

# Full model family for CP2 model benchmarking.
MODEL_BENCHMARK_CANDIDATES = [
    "LinearRegression",
    "Ridge",
    "Lasso",
    "ElasticNet",
    "DecisionTree",
    "ExtraTrees",
    "RandomForest",
    "XGBoost",
    "LightGBM",
    "CatBoost",
]


# ---------------------------------------------------------------------------
# Explicit research checkpoints (no human-gate YAML)
# ---------------------------------------------------------------------------

CP1_FEATURE_SET_SELECTION = "CP1_FEATURE_SET_SELECTION"
CP2_MODEL_SELECTION = "CP2_MODEL_SELECTION"
CP3_VALIDATED_CONFIGURATION_SELECTION = (
    "CP3_VALIDATED_CONFIGURATION_SELECTION"
)

RESEARCH_CHECKPOINTS = (
    CP1_FEATURE_SET_SELECTION,
    CP2_MODEL_SELECTION,
    CP3_VALIDATED_CONFIGURATION_SELECTION,
)


# ---------------------------------------------------------------------------
# Geometry / wind conventions shared with later utilities
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6_371_000.0

# Meteorological wind direction is the direction the wind COMES FROM.
RELATIVE_WIND_ANGLE_CONVENTION = {
    "headwind_deg": 0.0,
    "crosswind_deg": 90.0,
    "tailwind_deg": 180.0,
}

# route_bearing is an intermediate geometry variable only.
ROUTE_BEARING_IS_MODEL_FEATURE = False
