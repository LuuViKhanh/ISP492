# Drone Backend — Diagrams Index

Tổng hợp tất cả Class Diagram và Sequence Diagram được tạo bằng **Archify** từ source code thực của project.

Mỗi file HTML là một diagram tương tác: có thể zoom, click vào node, export PNG/SVG/WebM, chuyển dark/light theme.

---

## 3.1 Authentication, RBAC & Account Management

### 3.1.1 Class Diagram

| Diagram | File |
|---|---|
| Auth, RBAC & Account Management | [auth-rbac-class.html](.archify/class-auth-rbac-20261112-060000/auth-rbac-class.html) |

**Covers:** `Account` (alias `User`), `UserRole` (Enum — not a DB table), `reset_token` & `reset_token_expires_at` (embedded on Account), `AuthService` (module functions), `RoleChecker`, `CurrentUser`, `AuthRouter`, `AdminUsersRouter`.

**Key relationships:**
- `AuthRouter` → `AuthService` (calls), `Account` (queries)
- `AdminUsersRouter` → `RoleChecker` (requires ADMIN), `Account` (CRUD)
- `RoleChecker` → `CurrentUser` (validates role)
- `Account` → `UserRole` (role property)

---

### 3.1.2 SD — User Sign-In (JWT & Google OAuth2)

| Diagram | File |
|---|---|
| User Sign-In — JWT & Google OAuth2 | [signin-google-oauth.html](.archify/sequence-signin-google-oauth-20261112-060000/signin-google-oauth.html) |

**Endpoints:** `POST /api/v1/auth/login` · `GET /api/v1/auth/google` → `GET /api/v1/auth/google/callback`

**Segments:**
- **Path A — Email & Password:** query DB → bcrypt verify → jose.jwt sign (HS256) → `TokenResponse`
- **Path B — Google OAuth2:** redirect → user authenticates → exchange code → `get_or_create_google_user()` → tokens via URL params

**Error cases:** 401 (wrong credentials), 403 (suspended), redirect to `/login?error=google_failed`

---

### 3.1.3 SD — Password Recovery & Token Refresh

| Diagram | File |
|---|---|
| Password Recovery & Token Refresh | [pwd-recovery-token-refresh.html](.archify/sequence-pwd-recovery-token-refresh-20261112-060000/pwd-recovery-token-refresh.html) |

**Endpoints:** `POST /auth/forgot-password` · `POST /auth/reset-password` · `POST /auth/refresh`

**Segments:**
- **Phase 1:** `secrets.token_urlsafe(32)` → save with 15-min expiry → send email (anti-enumeration: same response always)
- **Phase 2:** verify token + expiry → bcrypt hash new pw → clear token (single-use)
- **Phase 3:** decode refresh token → verify `type=="refresh"` + `is_active` → rotate both tokens

---

### 3.1.4 SD — Admin User & Role Management

| Diagram | File |
|---|---|
| Admin User & Role Management | [admin-user-mgmt.html](.archify/sequence-admin-user-mgmt-20261112-060000/admin-user-mgmt.html) |

**Endpoints:** `POST /admin/users` · `GET /admin/users` · `PATCH /admin/users/{id}` · `DELETE /admin/users/{id}`

**Segments:**
- **Create User:** email duplicate check, archive if soft-deleted, set `role_id` via `ROLE_NAME_TO_ID`
- **List Users:** filter by `is_active` (true/false/null), ordered by `created_at` desc
- **Update User:** partial update — email (unique check), role, `is_active`; archive guard blocks reactivation of `archived_*` accounts
- **Soft Delete:** `is_active=False` only — no email change; audit log `ADMIN_SOFT_DELETE`; token immediately rejected

---

## 3.2 Order Management, Resource Readiness & PayOS Payment

### 3.2.1 Class Diagram

| Diagram | File |
|---|---|
| Order Management, Resource Readiness & PayOS Payment | [order-mgmt-class.html](.archify/class-order-mgmt-20261112-070000/order-mgmt-class.html) |

**Covers:** `Order` (SQLAlchemy model), `OrderStatus`, `DeliveryMode`, `ResourceReadinessService`, `PayOSClient`, `CustomerOrderRouter`, `OperatorOrderRouter`, `PaymentsRouter`.

**Key notes:**
- `Order` embeds all payment fields: `payment_order_code` (unique), `payment_qr_code`, `payment_checkout_url`
- Aging: `is_aging_priority`, `aging_deadline` (NORMAL mode only)
- `PayOSClient` wraps PayOS SDK (`get_payos()`)

---

### 3.2.2 SD — Fee Estimation & Hub Resource Readiness Check

| Diagram | File |
|---|---|
| Fee Estimation & Hub Resource Readiness | [fee-estimate-readiness.html](.archify/seq-fee-estimate-readiness-20261112-070000/fee-estimate-readiness.html) |

**Endpoint:** `POST /api/v1/customer/orders/estimate`

**Segments:**
- **① Validate & Distance:** Haversine only (no Google Maps), hub coordinates from DB
- **② AI Energy:** physics baseline + ML `dji_model.pkl` + SHAP; weather from static CSV (no Open-Meteo here)
- **③ Fee Calculation:** tiered pricing hardcoded (≤1/≤3/≤5/>5 kg); ETA by delivery mode
- **④ Hub Resource Readiness:** T_queue × 15min + T_charge (1%/min to 80%) + T_dispatch (0 / +20 / +30 min)

---

### 3.2.3 SD — Order Creation & PayOS Webhook Confirmation

| Diagram | File |
|---|---|
| Order Creation & PayOS Webhook Confirmation | [create-order-webhook.html](.archify/seq-create-order-webhook-20261112-070000/create-order-webhook.html) |

**Endpoints:** `POST /customer/orders` → `POST /payments/orders/{id}/checkout` → `POST /payments/webhook`

**Segments:**
- **Phase 1:** INSERT `Order` (PENDING, UNPAID, `delivery_fee=total_fee`); notify customer + operators
- **Phase 2:** idempotent checkout; `PayOS.createPaymentLink()`; save VietQR fields to DB
- **Phase 3:** HMAC-SHA256 verify → `code=="00"` → PAID + `PENDING→AWAITING_DROP_OFF`; anti-duplicate notification

---

### 3.2.4 SD — Order Handover at Origin Hub & Priority Scoring

| Diagram | File |
|---|---|
| Order Handover & Priority Scoring | [handover-priority.html](.archify/seq-handover-priority-20261112-070000/handover-priority.html) |

**Endpoints:** `POST /technician/deliveries/outbound/{orderId}/receive` · `GET /operator/orders`

**Segments:**
- **① Technician Receives Package:** guards (correct hub + PAID), cancel premature missions, `AWAITING_DROP_OFF → AT_HUB`
- **② Priority Scoring:** `coordination_deadline = w_end − (flight_mins + 30)` · `slack = coord − now` · `score = BasePriority + UrgencyScore` · level: CRITICAL/HIGH/MEDIUM/LOW

**Background:** Aging Priority Worker (15-min cron) escalates NORMAL orders past `aging_deadline` → `is_aging_priority=True` → base score 50 → HIGH

---

## 3.3 AI Route Optimization, Energy Prediction & Order Bundling

### 3.3.1 Class Diagram

| Diagram | File |
|---|---|
| AI Route Optimization, Energy Prediction & Order Bundling | [ai-route-class.html](.archify/class-ai-route-20261112-080000/ai-route-class.html) |

**Covers:** `Mission`, `MissionLeg`, `AIPrediction` (field `recommendation_content`), `WeatherData` (dataclass), `AIEnergyPredictionService`, `BundleService`, `AutoDispatchService`.

**Key notes:**
- `AIPrediction.recommendation_content`: stores AI reasoning snapshot at mission creation (route reason or `[Auto-Dispatch XAI]` summary)
- `MissionLeg.battery_id`: per-leg battery assignment (leg 0 = assigned battery, relay legs = swap battery)
- `BundleService` resolves effective origin hub for replan scenarios

---

### 3.3.2 SD — Multi-Order Bundle Validation

| Diagram | File |
|---|---|
| Multi-Order Bundle Validation | [bundle-validate.html](.archify/seq-bundle-validate-20261112-080000/bundle-validate.html) |

**Endpoint:** `POST /api/v1/operator/missions/bundle/validate-pre-analyze`

**Validation rules:** all orders AT_HUB · same origin+dest hub · no active mission · total payload ≤ drone capacity

**Response:** `{ is_valid, total_payload_kg, remaining_capacity_kg, orders_detail }`

---

### 3.3.3 SD — AI Route Optimization & Energy Prediction

| Diagram | File |
|---|---|
| AI Route Optimization & Energy Prediction | [route-optimize.html](.archify/seq-route-optimize-20261112-080000/route-optimize.html) |

**Endpoint:** `POST /api/v1/operator/missions/mission-planning/analyze`

**Segments (9 zones internally):**
- **① Validate & Resolve Origin:** replan uses incident hub as new origin
- **② Load Resources:** all active hubs + batteries; auto-select drone (cross-hub if needed)
- **③ Three Route Options:** Route 1 ECO (10 m/s, 45m) · Route 2 FAST (15 m/s, 60m) · Route 3 Relay (Dijkstra battery-swap) — all with real Open-Meteo weather
- **④ Feasibility & Recommend:** 80% safety reserve check; risk LOW/MEDIUM/HIGH; `recommend_mode` ECO or FAST

---

### 3.3.4 SD — Mission Confirmation, Resource Assign & Flight Leg Generation

| Diagram | File |
|---|---|
| Mission Confirmation, Resource Assign & Flight Leg Generation | [mission-confirm.html](.archify/seq-mission-confirm-20261112-080000/mission-confirm.html) |

**Endpoints:** `POST /operator/missions` · `POST /operator/missions/auto-dispatch`

**Segments:**
- **① Validate & Select Route:** PAID + AT_HUB required; infeasibility guard (400 if energy > 80% reserve)
- **② Create Records:** `Mission` (SCHEDULED, AT_HUB) → `MissionLeg` per segment (battery per leg) → `AIPrediction` snapshot → `Order.planning_at`
- **③ Auto-Dispatch (XAI SHAP):** Drone (locality 35, payload 25, readiness 25, util 15) + Battery (reserve 40, SoC 25, mount 20, health 15) + Route (safety 35, energy 25, SLA 25, simplicity 15); `dry_run` support

---

## 3.4 IoT Telemetry, Failsafe & Emergency Landing

### 3.4.1 Class Diagram

| Diagram | File |
|---|---|
| IoT Telemetry, Failsafe & Emergency Landing | [telemetry-failsafe-class.html](.archify/class-telemetry-failsafe-20261112-090000/telemetry-failsafe-class.html) |

**Covers:** `TelemetryLog` (20 fields), `HubCheckpointResponse` (stored in `Mission.checkpoints_json`), `FailsafeEvaluateResponse` (3-tier), `DroneSimulator` (`simulate_mission_flight`), `SignalLossWatcher` (`run_telemetry_signal_loss_watcher`), `MissionRouter`.

---

### 3.4.2 SD — Real-time IoT Telemetry Ingestion & 3-Tier Failsafe Check

| Diagram | File |
|---|---|
| Real-time IoT Telemetry Ingestion & 3-Tier Failsafe Check | [telemetry-failsafe.html](.archify/seq-telemetry-failsafe-20261112-090000/telemetry-failsafe.html) |

**Endpoints:** `POST /operator/missions/{id}/telemetry` · `POST /operator/missions/{id}/failsafe-eval`

**Segments:**
- **Phase 1 — Telemetry:** APPROVED→FLYING auto-transition; `fetch_weather()` per point (Open-Meteo, 5-min cache); INSERT `TelemetryLog` (11 IoT + 10 weather fields); hub proximity check ≤200m + 60s cooldown → `HubCheckpoint`
- **Phase 2 — Failsafe:** load drone/battery/latest_log/MissionLeg; `wind_factor = 1 + (wind−8)×0.12` when wind > 8 m/s; compute PNR; **Tier 0** SAFE/WARNING, **Tier 1** RTH (before PNR), **Tier 2** DIVERT (past PNR), **Tier 3** EMERGENCY_LAND (<10%)
- **Phase 3 — Signal Watcher:** every 3s; threshold 10s; dedup guard; INSERT `Incident HIGH` + `MaintenanceAlert`; notify operators + admins

---

### 3.4.3 SD — In-flight Incident Handling & Emergency Landing

| Diagram | File |
|---|---|
| In-flight Incident Handling & Emergency Landing | [emergency-land.html](.archify/seq-emergency-land-20261112-090000/emergency-land.html) |

**Endpoints:** `POST /operator/missions/{id}/emergency-land` · `POST /operator/missions/{id}/failsafe-execute`

**Segments:**
- **Path A — Operator:** FLYING/IN_PROGRESS required → `INCIDENT_RETURN` + `CANNOT_CONTINUE` → `Incident HIGH` + `MaintenanceAlert` → `Drone→MAINTENANCE` → notify operators + customer
- **Path B — Simulator:** `_trigger_incident()` at armed waypoint → `INCIDENT_RETURN` → `_fly_incident_return()` streams `TelemetryLog` along return path → `AT_HUB` for Technician

**Three incident types (simulator):**
- `FAILSAFE_RTH` — controlled return to nearest hub (recoverable)
- `SIGNAL_LOSS` — simulator stops; watcher detects gap > 10s
- `DISCONNECT_CRASH` — last log at crash coords (alt=0, speed=0); Mission→FAILED; Drone→MAINTENANCE

---

## Earlier Diagrams (Pre-feature structure)

These were created before the feature-based grouping above.

| Title | File |
|---|---|
| User Sign-In (JWT Authentication) | [user-signin-jwt.html](.archify/sequence-user-signin-jwt-20261112-000000/user-signin-jwt.html) |
| User Registration | [user-registration.html](.archify/sequence-user-registration-20261112-010000/user-registration.html) |
| Password Reset Workflow | [password-reset.html](.archify/sequence-password-reset-20261112-020000/password-reset.html) |
| Estimate Route, Energy & Delivery Fee | [estimate-route-energy-fee.html](.archify/sequence-estimate-route-energy-fee-20261112-030000/estimate-route-energy-fee.html) |
| Create Order & Generate PayOS QR Code | [create-order-payos.html](.archify/sequence-create-order-payos-20261112-040000/create-order-payos.html) |
| PayOS Webhook Payment Confirmation | [payos-webhook.html](.archify/sequence-payos-webhook-20261112-050000/payos-webhook.html) |

---

## Quick Reference — All Files

```
.archify/
├── class-auth-rbac-20261112-060000/           auth-rbac-class.html          (3.1.1)
├── sequence-signin-google-oauth-20261112-060000/ signin-google-oauth.html   (3.1.2)
├── sequence-pwd-recovery-token-refresh-20261112-060000/ pwd-recovery-token-refresh.html (3.1.3)
├── sequence-admin-user-mgmt-20261112-060000/  admin-user-mgmt.html          (3.1.4)
├── class-order-mgmt-20261112-070000/          order-mgmt-class.html         (3.2.1)
├── seq-fee-estimate-readiness-20261112-070000/ fee-estimate-readiness.html  (3.2.2)
├── seq-create-order-webhook-20261112-070000/  create-order-webhook.html     (3.2.3)
├── seq-handover-priority-20261112-070000/     handover-priority.html        (3.2.4)
├── class-ai-route-20261112-080000/            ai-route-class.html           (3.3.1)
├── seq-bundle-validate-20261112-080000/       bundle-validate.html          (3.3.2)
├── seq-route-optimize-20261112-080000/        route-optimize.html           (3.3.3)
├── seq-mission-confirm-20261112-080000/       mission-confirm.html          (3.3.4)
├── class-telemetry-failsafe-20261112-090000/  telemetry-failsafe-class.html (3.4.1)
├── seq-telemetry-failsafe-20261112-090000/    telemetry-failsafe.html       (3.4.2)
└── seq-emergency-land-20261112-090000/        emergency-land.html           (3.4.3)
```

---

*Generated from source code at commit `7b63ec3c99e7f74462bd6023266ffdf42088a896` using Archify v3.2.0.*
