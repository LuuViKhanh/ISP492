"""Configuration for the trajectory-derived Multi-Hub routing experiment."""

import os

from config.multiuav_config import BASE_PREFLIGHT_FEATURES

# Graph topology / virtual-hub placement
GRAPH_DIRECTED = True
HUB_PLACEMENT_METHOD = "METHOD_C_ON_ROUTE"
ON_ROUTE_HUB_INTERVAL_M = float(
    os.environ.get("RS_HUB_INTERVAL_M", "200.0")
)
HUB_DISTANCE_THRESHOLD_M = float(
    os.environ.get("RS_HUB_CLUSTER_M", "15.0")
)
MIN_HUB_SUPPORT = 1
MIN_SEGMENT_DISTANCE_M = 50.0

# Candidate-path constraints
MAX_PATH_HOPS = 10
# General graph-path detour control used by G12 relative to the graph shortest path.
MAX_PATH_DISTANCE_RATIO = 1.5
# Expanded topology scenarios are generated independently of historical
# missions. This is the reviewer-facing minimum, not a sampling cap.
MIN_RECOMMENDED_UNIQUE_OD_PAIRS = 10
EXPAND_TOPOLOGY_OD_PAIRS = True
# Final same-mission RQ5 plausibility control relative to the ACTUAL historical
# mission distance. This prevents maximizing EE by selecting an arbitrarily long
# detour simply because distance appears in the EE numerator.
FINAL_MAX_DISTANCE_RATIO_VS_HISTORICAL = 1.5

# Balanced operational routing. Scores are normalized within each request's
# candidate set so Wh and m/Wh are comparable without mixing physical units.
MULTI_OBJECTIVE_EE_WEIGHT = 0.50
MULTI_OBJECTIVE_ENERGY_WEIGHT = 0.50
MULTI_OBJECTIVE_WEIGHT_SENSITIVITY = (
    (0.25, 0.75),
    (0.50, 0.50),
    (0.75, 0.25),
)

# Increment when persisted routing outputs change schema. Sensitivity runs use
# this to avoid reusing stale policy tables from an older code bundle.
ROUTING_POLICY_SCHEMA_VERSION = 3

# Modeling
USE_EXISTING_RELATIVE_WIND_ANGLE = False
RANDOM_STATE = 42
COMMON_PREFLIGHT_FEATURES = list(BASE_PREFLIGHT_FEATURES)

# One-factor-at-a-time graph-construction sensitivity requested by the study
# protocol. The 200 m / 15 m configuration is the baseline.
GRAPH_SENSITIVITY_CONFIGS = (
    (150.0, 15.0),
    (200.0, 15.0),
    (250.0, 15.0),
    (200.0, 10.0),
    (200.0, 20.0),
)
