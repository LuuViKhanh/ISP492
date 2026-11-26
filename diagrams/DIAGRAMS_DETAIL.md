# Drone Backend — Diagrams Flow Description

Mô tả nội dung từng diagram theo flow từ đầu đến cuối, viết dạng văn xuôi.

---

## 3.1 Authentication, RBAC & Account Management

### 3.1.1 Class Diagram — Auth, RBAC & Account Management

> [auth-rbac-class.html](.archify/class-auth-rbac-20261112-060000/auth-rbac-class.html)

Hệ thống xác thực xoay quanh hai router chính. `AuthRouter` (prefix `/auth`) xử lý toàn bộ luồng đăng nhập, đăng ký, refresh token, quên/đặt lại mật khẩu và Google OAuth. `AdminUsersRouter` (prefix `/admin/users`) đảm nhiệm quản lý người dùng toàn hệ thống và bắt buộc phải có role ADMIN thông qua `RoleChecker` đặt ở mức router — tức là mọi endpoint trong đó đều được bảo vệ mà không cần khai báo lại.

Cả hai router đều phụ thuộc vào `AuthService`, một module thuần hàm (không phải class instance) chứa tất cả logic nghiệp vụ: hash và verify password bằng bcrypt, tạo và decode JWT bằng jose.jwt theo chuẩn HS256, truy vấn tài khoản theo email hoặc ID, tạo reset token, và thực hiện reset password.

Khi một request đi vào protected endpoint, FastAPI inject `CurrentUser` (Pydantic record gồm id, email, role, hub_id) thông qua `Depends(get_current_user)`. Hàm `get_current_user` decode JWT, verify `type == "access"`, rồi kiểm tra `user.is_active` — nếu tài khoản bị soft-delete thì token lập tức bị từ chối với 401. `RoleChecker` nhận danh sách `allowed_roles` và so sánh `CurrentUser.role` — không khớp thì 403.

Model `Account` (alias `User`) ánh xạ bảng `accounts` trong DB. Điểm đáng chú ý: `role_id` lưu string key như `"ADMIN"` hay `"TECHNICIAN"`, không phải UUID mặc dù tên field gợi ý vậy. `UserRole` là Python Enum thuần tuý, không có bảng trong DB. Hai field `reset_token` và `reset_token_expires_at` được nhúng thẳng lên Account thay vì tách riêng. `hub_id` (FK → hubs) chỉ được set qua seed SQL, không có API endpoint để gán.

---

### 3.1.2 SD — User Sign-In (JWT & Google OAuth2)

> [signin-google-oauth.html](.archify/sequence-signin-google-oauth-20261112-060000/signin-google-oauth.html)

Diagram này gộp hai luồng đăng nhập vào một màn hình để so sánh song song.

**Path A — Email & Password:** Client gửi `POST /auth/login` với email và password. Auth Router gọi `get_user_by_email()` để tra cứu tài khoản trong DB. Nếu không tìm thấy user hoặc user không có `hashed_password`, router ngay lập tức raise 401. Nếu có, router chuyển plain password và hashed password cho bcrypt verify. bcrypt trả về True hoặc False — nếu False, 401; nếu user đang `is_active=False`, raise 403 với thông báo tài khoản bị xóa. Khi vượt qua tất cả kiểm tra, router gọi `create_access_token` và `create_refresh_token` trên jose.jwt với payload `{sub, role, type}`. Access token hết hạn sau 60 phút, refresh token sau 7 ngày, cả hai dùng HS256. Response là `200 TokenResponse` chứa cả hai token cùng role.

**Path B — Google OAuth2:** Client gọi `GET /auth/google`, router redirect ngay sang Google Authorization URL với scope `openid email profile`. Sau khi user xác thực trên Google, Google redirect về `GET /auth/google/callback?code=...`. Router gọi `exchange_google_code(code)` trong service, service dùng httpx để POST tới Google token URL đổi lấy access token rồi lấy userinfo. Thông tin Google trả về được đưa vào `get_or_create_google_user()` — nếu email đã tồn tại thì trả về account cũ, nếu chưa thì tạo mới. Sau đó tạo token pair và redirect người dùng về frontend với access_token và refresh_token trong URL params. Nếu exchange code lỗi, redirect về `/login?error=google_failed`.

---

### 3.1.3 SD — Password Recovery & Token Refresh

> [pwd-recovery-token-refresh.html](.archify/sequence-pwd-recovery-token-refresh-20261112-060000/pwd-recovery-token-refresh.html)

Diagram chia ba phase độc lập.

**Phase 1 — Forgot Password:** Client gửi `POST /auth/forgot-password` với email. Service gọi `create_reset_token()` — đầu tiên tra cứu user theo email trong DB. Nếu không tìm thấy hoặc `is_active=False`, hàm return `None` ngay lập tức mà không báo lỗi. Nếu user hợp lệ, service tạo token bằng `secrets.token_urlsafe(32)` (không phải JWT, là random string), lưu token cùng `reset_token_expires_at = now + 15 phút` vào DB. Token được trả về router, router gọi `send_reset_email()` với link `{FRONTEND_URL}/reset-password?token=...`. Dù user có tồn tại hay không, response luôn là `200` với cùng một message — đây là biện pháp chống email enumeration.

**Phase 2 — Reset Password:** Client gửi `POST /auth/reset-password` với token và new_password. Service query DB với điều kiện `reset_token = :token AND expires_at > now()`. Nếu không tìm thấy record hoặc hết hạn, trả 400. Nếu hợp lệ, service hash mật khẩu mới bằng bcrypt rồi UPDATE `password_hash`, đồng thời set `reset_token = NULL` và `reset_token_expires_at = NULL` — đảm bảo token chỉ dùng được một lần. Response `200 Password reset successfully`.

**Phase 3 — Token Refresh:** Client gửi `POST /auth/refresh` kèm refresh_token. Service decode token bằng `decode_token()`, kiểm tra `payload["type"] == "refresh"` — nếu không phải 401. Tiếp theo service tra cứu user theo `payload["sub"]` trong DB, kiểm tra `is_active`. Nếu hợp lệ, tạo cặp token mới hoàn toàn (cả access lẫn refresh đều được rotate) và trả về. Refresh token cũ trở nên vô hiệu về mặt logic vì hệ thống stateless — không có blacklist.

---

### 3.1.4 SD — Admin User & Role Management

> [admin-user-mgmt.html](.archify/sequence-admin-user-mgmt-20261112-060000/admin-user-mgmt.html)

Mọi request vào router đều đi qua `RoleChecker([ADMIN])` đặt ở mức router trước — không có bất kỳ endpoint nào bỏ qua bước này.

**Create User:** Admin gửi `POST /admin/users` với `UserCreate` (email, full_name, password, role). Router gọi `get_user_by_email()` kiểm tra xem email đã tồn tại chưa. Nếu tồn tại và `is_active=True`, raise 400. Nếu tồn tại nhưng `is_active=False` (đã soft-delete), hệ thống archive email cũ bằng cách prefix `archived_{timestamp}_` vào email đó để giải phóng email gốc, sau đó tạo account mới. `role_id` được ánh xạ qua `ROLE_NAME_TO_ID[body.role]` — không set `hub_id` vì API không hỗ trợ.

**List Users:** `GET /admin/users?is_active=true/false/null` — query thẳng vào DB với filter `is_active`, sắp xếp theo `created_at` giảm dần. Trả `list[ProfileResponse]`.

**Update User:** `PATCH /admin/users/{id}` nhận `UserAdminUpdate` (tất cả field Optional). Router tra cứu user, nếu không có thì 404. Với từng field không null: email thì check unique; full_name gán trực tiếp; role thì map `ROLE_NAME_TO_ID`; is_active có guard đặc biệt — nếu cố reactivate một account có email bắt đầu bằng `archived_` hoặc `deleted_`, raise 400 vì account đó đã bị archive vĩnh viễn và không thể khôi phục.

**Soft Delete:** `DELETE /admin/users/{id}` chỉ set `is_active = False` — không đổi email, không xóa record. Mục đích giữ toàn vẹn FK với orders, missions, v.v. Sau commit, ghi audit log `ADMIN_SOFT_DELETE` vào `activity_logs`. Token cũ của user đó bị từ chối ngay lần request tiếp theo vì `get_current_user` check `is_active`.

---

## 3.2 Order Management, Resource Readiness & PayOS Payment

### 3.2.1 Class Diagram — Order Management, Resource Readiness & PayOS Payment

> [order-mgmt-class.html](.archify/class-order-mgmt-20261112-070000/order-mgmt-class.html)

Phần này xoay quanh model `Order` và ba router phục vụ nó từ ba góc độ khác nhau.

`CustomerOrderRouter` (prefix `/customer`) là điểm vào từ phía khách hàng: estimate giá trước, tạo đơn, và xem danh sách đơn của mình. Khi tạo đơn, router dùng `ResourceReadinessService` để đánh giá xem hub xuất phát có đủ drone và pin không — kết quả ảnh hưởng trực tiếp vào khung giờ giao hàng cam kết với khách.

`OperatorOrderRouter` (prefix `/operator/orders`) cho phép Operator xem danh sách tất cả đơn, lấy chi tiết một đơn, tìm drone khả dụng, và huỷ đơn. Router này còn chứa hàm `compute_urgency_fields()` — tính `priority_score` và `coordination_deadline` cho từng đơn để Operator biết đơn nào cần lên lịch bay gấp.

`PaymentsRouter` (prefix `/payments`) quản lý toàn bộ vòng đời thanh toán: tạo link PayOS, nhận webhook xác nhận, kiểm tra trạng thái, huỷ link, và hoàn tiền. Router này dùng `PayOSClient` — một wrapper mỏng quanh SDK `payos==0.1.7` với hàm `get_payos()` khởi tạo instance từ credentials trong config.

Model `Order` nhúng thẳng tất cả payment fields (`payment_order_code`, `payment_qr_code`, `payment_checkout_url`, v.v.) thay vì tách bảng riêng. Nó cũng có hai field aging priority (`is_aging_priority`, `aging_deadline`) chỉ dùng cho delivery mode NORMAL. `OrderStatus` và `DeliveryMode` là Python Enum, không phải DB table.

---

### 3.2.2 SD — Fee Estimation & Hub Resource Readiness Check

> [fee-estimate-readiness.html](.archify/seq-fee-estimate-readiness-20261112-070000/fee-estimate-readiness.html)

Khi khách hàng muốn biết giá và khung giờ giao trước khi đặt, họ gọi `POST /customer/orders/estimate`. Endpoint này không yêu cầu JWT.

Đầu tiên router validate payload trong khoảng 0.1–10 kg, rồi lấy tọa độ hai hub từ DB. Khoảng cách giữa hai hub được tính bằng công thức Haversine thuần toán học (R=6371 km) — không gọi Google Maps hay bất kỳ API bản đồ nào.

Tiếp theo router gọi `predict_flight_energy()` trong AI Service với khoảng cách và payload vừa tính được. Hàm này không gọi Open-Meteo — thay vào đó nó chọn một hàng thời tiết từ file CSV tĩnh (`weather_test_set.csv`) theo công thức deterministic `row_idx = int(dist*10 + payload*3) % len`. Dữ liệu thời tiết này được đưa vào mô hình vật lý kết hợp ML (`dji_model.pkl`) cùng SHAP corrections để ra giá trị `energy_wh`.

Sau đó router gọi `calculate_delivery_cost()` — hàm hardcode bảng giá theo tầng payload (≤1/≤3/≤5/>5 kg), cộng phí khoảng cách và tải trọng, rồi tính thêm phụ phí Fragile +15%, Express +25%, Insurance +10%. ETA được tính inline ngay trong bước này tuỳ theo delivery mode: EXPRESS có deadline 3 giờ, NORMAL có SLA 4 giờ, SCHEDULED theo thời gian khách chọn.

Cuối cùng router gọi `evaluate_hub_resource_readiness()` — query ba lần vào DB để đếm đơn đang chờ tại hub (T_queue × 15 phút mỗi đơn), kiểm tra pin nào đủ 80% (T_charge = thời gian sạc còn thiếu ở tốc độ 1%/phút), và xem drone local hay phải điều phối từ hub khác (T_dispatch: 0 / +20 / +30 phút). `total_extra_wait = max(T_queue, T_charge) + T_dispatch` được cộng vào ETA. Response cuối gồm giá, khoảng cách, ETA đã điều chỉnh, và `customer_notice` giải thích nếu có độ trễ.

---

### 3.2.3 SD — Order Creation & PayOS Webhook Confirmation

> [create-order-webhook.html](.archify/seq-create-order-webhook-20261112-070000/create-order-webhook.html)

Luồng này gồm ba phase nối tiếp nhau: tạo đơn, tạo link thanh toán, và xác nhận webhook.

**Phase 1 — Tạo đơn:** Khách gửi `POST /customer/orders` kèm JWT. Router validate payload (0.1–10 kg), lấy tọa độ hai hub, tính Haversine + phí tương tự flow estimate. Sau khi có `total_fee`, router đánh giá hub readiness để tính extra wait, rồi tính khung giờ giao hàng theo delivery mode. Đơn được INSERT vào DB với `status=PENDING`, `payment_status=UNPAID`, và `delivery_fee=total_fee` (đã set sẵn, không cần operator xác nhận). Sau commit, router gửi notification cho khách ("Order Created") và tất cả Operator ("New Order"). Response `201 OrderCreateResponse` chứa order_id và estimated_window.

**Phase 2 — Tạo link PayOS:** Khách gọi `POST /payments/orders/{id}/checkout` với JWT. Router lấy order từ DB, kiểm tra quyền (chỉ owner hoặc Admin mới được pay), guard `payment_status == PAID` → 400, guard `delivery_fee` chưa set → 400. Nếu `payment_status` đang là `PENDING` và đã có `checkout_url` trong DB → trả lại link cũ ngay (idempotent). Nếu chưa, tạo `order_code = int(time.time()*1000) % 9999999 + random(1,999)`, gọi `PayOS.createPaymentLink()` với amount, return_url, cancel_url. PayOS trả về `checkoutUrl`, `qrCode`, `bin`, `accountNumber`. Router lưu tất cả vào DB và update `payment_status=PENDING`. Response `200 CheckoutResponse` với `checkout_url` và `qr_code` để frontend hiển thị.

**Phase 3 — Webhook xác nhận:** PayOS gọi `POST /payments/webhook` sau khi khách thanh toán. Router đọc raw body, parse JSON, lấy `signature` và `data`. Nếu `data` rỗng (đây là lời gọi verify khi đăng ký webhook) → trả 200 ngay. Nếu không, build canonical string bằng cách sort key của `data` alphabetically, normalize null/undefined thành empty string, rồi tính HMAC-SHA256 với `PAYOS_CHECKSUM_KEY` và so sánh case-insensitive với received signature. Sai → 400. Đúng → tìm order theo `payment_order_code` (không phải order_id). Nếu `code == "00"` (mã thành công của PayOS): set `payment_status=PAID`, `paid_at=now`, nếu `order.status == PENDING` thì chuyển sang `AWAITING_DROP_OFF`, gửi notification cho khách "mang hàng đến hub". Ngược lại: `payment_status=FAILED`. Luôn trả 200 cho PayOS kể cả khi không tìm thấy order.

---

### 3.2.4 SD — Order Handover at Origin Hub & Priority Scoring

> [handover-priority.html](.archify/seq-handover-priority-20261112-070000/handover-priority.html)

Sau khi khách đã thanh toán và đơn ở trạng thái `AWAITING_DROP_OFF`, khách mang hàng đến hub gửi. Kỹ thuật viên tại hub xác nhận nhận hàng qua `POST /technician/deliveries/outbound/{orderId}/receive`.

Router kiểm tra hai điều kiện quan trọng: Kỹ thuật viên phải có `hub_id` khớp với `order.origin_hub_id` — nếu sai hub thì 403. Và đơn phải đã `payment_status == PAID` — chưa thanh toán thì 400. Nếu đơn đã ở `AT_HUB` rồi (idempotent call), chỉ fill thêm các field còn thiếu rồi trả về. Trường hợp bình thường, router cancel các mission "premature" (tạo sớm từ seed/legacy khi đơn còn PENDING) để giải phóng drone và pin, rồi UPDATE `order.status = AT_HUB`, `origin_received_at = now`, `origin_received_by = user.id`. Gửi notification cho khách ("Package Received") và tất cả Operator ("Ready for Planning").

Từ đây Operator mở danh sách đơn qua `GET /operator/orders`. Router query DB lấy tất cả đơn chưa cancelled, sau đó tính `compute_urgency_fields()` cho từng đơn: `coordination_deadline = estimated_window_end - (flight_mins + 30)`, `slack = coordination_deadline - now`, `BasePriority` theo mode (EXPRESS=60, SCHEDULED=35, NORMAL=10), `UrgencyScore = min(100, max(0, 100 × (60 - slack) / 60))`, `priority_score = Base + Urgency`. Level: CRITICAL (slack≤0) / HIGH (≤30) / MEDIUM (≤60) / LOW (>60). Operator thấy ngay đơn nào cần lên lịch bay gấp nhất.

Chạy ngầm mỗi 15 phút, Aging Priority Worker quét các đơn NORMAL đã qua `aging_deadline` mà chưa có mission — set `is_aging_priority=True`, nâng base score lên 50 (ngang EXPRESS), gửi cảnh báo cho Operator và khách.

---

## 3.3 AI Route Optimization, Energy Prediction & Order Bundling

### 3.3.1 Class Diagram — AI Route Optimization, Energy Prediction & Order Bundling

> [ai-route-class.html](.archify/class-ai-route-20261112-080000/ai-route-class.html)

Phần này xoay quanh ba service và ba model DB.

`AIEnergyPredictionService` là hàm `predict_flight_energy()` trong `ai_predictions/service.py` — deterministic và physics-informed. Nhận khoảng cách, payload, speed, altitude và weather_override, nó tính `base_eco_energy = dist × (15 Wh + 2.2 Wh/kg payload)`, áp các SHAP corrections (tốc độ, độ cao, gió), rồi blend với ML model `dji_model.pkl` nếu available. Cùng bộ tham số luôn ra cùng `energy_wh` — đảm bảo nhất quán giữa estimate, analyze, create mission và flight simulator.

`BundleService` xử lý logic gom đơn theo hai luồng: Pre-Analyze (gom trước khi tính tuyến) và Post-Analyze (ghép thêm sau khi đã chọn drone và route). Hàm quan trọng nhất là `validate_bundle_orders()` kiểm tra tất cả đơn cùng hub, AT_HUB, không có active mission. Hàm `resolve_effective_origin_hub_id()` đặc biệt hữu ích khi replan sau sự cố — nó tìm mission gần nhất kết thúc bằng `INCIDENT_RETURN` và dùng `destination_hub_id` của mission đó làm hub xuất phát mới thay vì hub gốc của đơn hàng.

`AutoDispatchService` thay thế hoàn toàn luồng chọn drone/pin/route thủ công. Nó dùng Additive SHAP — không phải hộp đen — để giải thích từng quyết định: chọn drone theo locality/payload/readiness/fleet_utilization, chọn pin theo reserve_margin/SoC/mount_readiness/lifecycle_health, chọn route theo safety/energy/SLA/simplicity. Với mỗi quyết định, `_compute_additive_shap()` đảm bảo `base_value + sum(shap_values) == total_score` (tiên đề Efficiency của Shapley).

Về model DB: `Mission` là bản ghi chuyến bay chính với drone_id, battery_id, order_id và status. `MissionLeg` biểu diễn từng chặng bay (from_hub → to_hub) với battery_id riêng per leg — leg 0 dùng pin gắn trên drone, relay legs dùng pin thay thế tại hub trung gian. `AIPrediction` lưu snapshot AI tại thời điểm tạo mission, đặc biệt field `recommendation_content` chứa lý do chọn route (manual) hoặc `[Auto-Dispatch XAI] executive_summary` (auto-dispatch).

---

### 3.3.2 SD — Multi-Order Bundle Validation

> [bundle-validate.html](.archify/seq-bundle-validate-20261112-080000/bundle-validate.html)

Trước khi phân tích tuyến bay cho nhiều đơn gộp chung, Operator gọi `POST /operator/missions/bundle/validate-pre-analyze` để xác minh rằng gói đơn đó hợp lệ.

Router chuyển danh sách `orderIds` và (tùy chọn) `droneId` cho `BundleService.validate_bundle_orders()`. Service đầu tiên query tất cả mission đang hoạt động để lấy set `active_order_ids` — dùng để loại bỏ các đơn đã được gán mission khác. Với mỗi order_id trong danh sách, service query DB: nếu không tìm thấy thì 404; nếu không ở trạng thái `AT_HUB` thì 400; nếu đã có active mission thì 400. Service cũng kiểm tra tất cả đơn phải cùng `origin_hub_id` và `destination_hub_id` — nếu có đơn lệch hub thì 400 kèm message chỉ rõ.

Sau khi validate từng đơn, service tính `total_payload_kg` và `total_delivery_fee`. Nếu có `droneId`, service query drone và so sánh total payload với `payload_capacity_kg` của drone — nếu vượt quá thì `is_valid=False` cùng message gợi ý chọn drone lớn hơn hoặc bớt đơn. Response cuối cho biết `is_valid`, `total_payload_kg`, và `remaining_capacity_kg`.

---

### 3.3.3 SD — AI Route Optimization & Energy Prediction

> [route-optimize.html](.archify/seq-route-optimize-20261112-080000/route-optimize.html)

Operator gọi `POST /operator/missions/mission-planning/analyze` để lấy các phương án tuyến bay có kèm giải thích AI.

Router trước tiên lấy order từ DB, rồi gọi `resolve_effective_origin_hub_id()` để xác định hub xuất phát thực tế — quan trọng khi đây là chuyến replan sau sự cố, hub xuất phát có thể khác hub gốc của đơn. Tiếp theo router load toàn bộ hub đang ACTIVE và tất cả pin ACTIVE từ DB, kiểm tra pin nào đang bị giữ chỗ bởi mission khác (virtual holds). Nếu không chỉ định drone, hệ thống tự chọn: ưu tiên drone local tại origin hub có pin 100% sẵn sàng; nếu không có, kích hoạt Cross-Hub Dispatch — tìm drone ở hub khác gần nhất cũng có pin đủ.

Với mỗi chặng bay tiềm năng, router gọi `fetch_weather()` song song để lấy thời tiết thực từ Open-Meteo (cache 5 phút). Thời tiết này được đưa vào `predict_flight_energy()` cùng khoảng cách và payload để tính năng lượng thực tế.

Hệ thống tạo ba phương án: **Route 1 ECO** bay thẳng ở 10 m/s (~36 km/h) độ cao 45m — tối ưu năng lượng; **Route 2 FAST** bay thẳng ở 15 m/s (~54 km/h) độ cao 60m — năng lượng cao hơn do lực cản tăng theo v^1.8; **Route 3 Relay** qua hub trung gian đổi pin, dùng Dijkstra pathfinding với edge cost là năng lượng hoặc thời gian. Mỗi route đều kiểm tra feasibility: nếu năng lượng cần > 80% năng lượng pin → `feasible=False`. Risk level: LOW (<60% pin dùng), MEDIUM (≤80%), HIGH (>80% hoặc bad weather). Route bị bad weather (gió ≥8 m/s, gust ≥11 m/s, mưa ≥2mm) bị loại khỏi recommendation. Route được recommend là route feasible có energy/duration tốt nhất theo `recommend_mode` (ECO → min energy, FAST → min duration).

---

### 3.3.4 SD — Mission Confirmation, Resource Assign & Flight Leg Generation

> [mission-confirm.html](.archify/seq-mission-confirm-20261112-080000/mission-confirm.html)

Sau khi Operator đã chọn route từ `/analyze`, họ tạo mission qua `POST /operator/missions`.

Router bắt đầu bằng loạt validate: order phải `PAID` và `AT_HUB`, không có active mission nào cho đơn này, và không có incident chưa được Technician confirm thu hồi (nếu có thì phải chờ, tránh tạo mission trong khi drone/hàng chưa về hub). Nếu có `bundledOrderIds`, gọi `validate_bundle_orders()` để lấy tổng payload. Route được chọn theo `routeId` trong request; nếu không có thì lấy route `recommended=True` và `feasible=True`; guard cuối cùng: nếu route `infeasible` thì raise 400 — không cho tạo mission với pin không đủ dự phòng 20%.

Sau validate, router INSERT `Mission` với `status=SCHEDULED`, `handling_status=AT_HUB`, drone và battery đã gán. Tiếp theo INSERT `MissionLeg` cho từng chặng trong `legs_detail`: leg đầu (index 0) dùng `assigned_battery.id`, các leg relay thì validate pin thay thế tại relay hub (phải đúng hub, đủ sạc, tương thích model, chưa bị reserve, đủ energy cho chặng đó). INSERT `AIPrediction` với năng lượng, thời gian, risk và `recommendation_content`. UPDATE `order.planning_at = now`, clear `replan_required_at`. Commit xong, gửi in-app notification cho khách và email cho receiver. Response `201 MissionResponse`.

**Auto-Dispatch path:** Nếu dùng `POST /operator/missions/auto-dispatch`, router gọi `run_auto_dispatch_selection()` trong AutoDispatchService. Service tự chạy toàn bộ flow chọn drone → pin → route với SHAP explanation, trả về lựa chọn tối ưu kèm `xai_shap_report`. Nếu `dry_run=True`, chỉ preview kết quả AI mà không tạo bản ghi. Nếu `dry_run=False`, build `MissionCreateRequest` từ kết quả và gọi lại `_create_mission_internal()` với `precomputed_routes` và `custom_recommendation_content = "[Auto-Dispatch XAI] {executive_summary}"`.

---

## 3.4 IoT Telemetry, Failsafe & Emergency Landing

### 3.4.1 Class Diagram — IoT Telemetry, Failsafe & Emergency Landing

> [telemetry-failsafe-class.html](.archify/class-telemetry-failsafe-20261112-090000/telemetry-failsafe-class.html)

Hệ thống giám sát bay thời gian thực xây dựng trên ba thành phần chính.

`TelemetryLog` là model DB với 20 field: 11 field IoT (GPS, altitude, speed, battery_voltage, battery_pct, energy_consumed_wh, wind_speed) và 10 field thời tiết từ Open-Meteo (temperature, apparent_temp, dew_point, humidity, wind_speed, wind_gust, wind_direction, precipitation, pressure, cloud_cover). Log được insert bởi cả API endpoint (batch từ drone thực) lẫn DroneSimulator (mỗi 3 giây per waypoint).

`HubCheckpointResponse` là Pydantic schema không có bảng riêng trong DB — được lưu dưới dạng JSON array trong `Mission.checkpoints_json`. Mỗi checkpoint ghi lại tọa độ drone tại thời điểm bay trong vòng 200m của một hub, kèm khoảng cách Haversine tới tâm hub và timestamp. Cooldown 60 giây tránh log trùng cho cùng hub.

`FailsafeEvaluateResponse` chứa quyết định của 3-Tier Failsafe Engine: `status` (SAFE/WARNING/RTH_TRIGGERED/DIVERT_TRIGGERED/EMERGENCY_LAND_TRIGGERED), `tier` (0-3), `action` (CONTINUE/RETURN_TO_HOME/DIVERT_TO_HUB/EMERGENCY_LAND), `pnr_reached` (đã qua điểm không thể quay đầu chưa), `recommended_hub` (hub an toàn gợi ý), và `divert_route` (6 waypoints nội suy từ vị trí hiện tại tới hub mục tiêu).

`DroneSimulator` là background coroutine (`asyncio.create_task`) được trigger khi Technician bấm Dispatch. Nó nội suy 20 waypoints giữa hai hub, mỗi 3 giây insert một TelemetryLog và cập nhật pin drone/battery trong DB. Có ba loại sự cố có thể xảy ra: `FAILSAFE_RTH` (bay về hub gần nhất có kiểm soát), `SIGNAL_LOSS` (dừng phát telemetry hoàn toàn), `DISCONNECT_CRASH` (insert log cuối tại tọa độ rơi với altitude=0).

`SignalLossWatcher` chạy trong vòng lặp asyncio mỗi 3 giây. Nó scan tất cả mission đang FLYING với `handling_status=NULL/INCOMING`, lấy TelemetryLog mới nhất của từng mission, và nếu log đó cũ hơn 10 giây so với now thì tạo `Incident HIGH` và `MaintenanceAlert` (với dedup guard tránh tạo trùng), gửi SYSTEM_ALERT notification cho tất cả Operator và Admin.

---

### 3.4.2 SD — Real-time IoT Telemetry Ingestion & 3-Tier Failsafe Check

> [telemetry-failsafe.html](.archify/seq-telemetry-failsafe-20261112-090000/telemetry-failsafe.html)

**Phase 1 — Telemetry Ingestion:** Drone hoặc Simulator gửi batch `POST /operator/missions/{id}/telemetry`. Router lấy mission, kiểm tra status phải là `APPROVED` hoặc `FLYING`. Nếu đang là `APPROVED`, tự động chuyển sang `FLYING`, set `start_time` và `departed_at`, đồng thời UPDATE order liên kết sang `IN_DELIVERY`. Với mỗi điểm telemetry trong batch, router gọi `fetch_weather(lat, lng)` tới Open-Meteo (non-blocking, timeout 3s, cache 5 phút) và tạo `TelemetryLog` với đủ 20 field. Sau khi INSERT logs, router query tất cả hub có tọa độ và với mỗi điểm telemetry, tính khoảng cách Haversine tới từng hub — nếu ≤200m và chưa có checkpoint cho hub đó trong 60 giây qua, tạo `HubCheckpointResponse` và append vào `mission.checkpoints_json`. Response `200 TelemetryIngestResponse` chứa số log đã lưu, danh sách log, danh sách checkpoint mới và trạng thái mission.

**Phase 2 — 3-Tier Failsafe Evaluation:** Operator (hoặc hệ thống) gọi `POST /operator/missions/{id}/failsafe-eval`. Router lấy drone, battery, TelemetryLog mới nhất, và MissionLeg đang IN_PROGRESS. Tọa độ hiện tại ưu tiên từ `body.current_lat/lng`, fallback sang log mới nhất. Pin hiện tại từ `body.override_battery_pct` hoặc `drone.battery_level_pct`. Gió từ `body.override_wind_speed` hoặc `latest_log.weather_wind_speed`. Khi `wind > 8 m/s`, hệ thống tính `wind_factor = 1 + (wind - 8) × 0.12` làm tăng mức tiêu hao pin per km. Tính `rem_at_dest` (pin còn lại khi tới đích) và `rem_at_origin` (pin còn lại khi quay về). Điểm không thể quay đầu (PNR) đạt được khi `dist_origin ≥ dist_dest` hoặc `rem_at_origin < 20%`. Router cũng scan tất cả hub để tìm hubs an toàn trong tầm. Ra quyết định: Tier 3 khi pin < 10% hoặc không có hub an toàn nào; Tier 1 RTH khi chưa qua PNR và `rem_at_dest < 20%`; Tier 2 DIVERT khi đã qua PNR và có hub gần; Tier 0 WARNING hoặc SAFE còn lại. Nếu action là RTH hoặc DIVERT, router nội suy 6 waypoints từ vị trí hiện tại tới hub mục tiêu làm `divert_route`.

**Phase 3 — Signal Loss Watcher:** Chạy ngầm mỗi 3 giây. Mỗi vòng lặp, query tất cả mission FLYING với handling_status cho thấy drone đang trên không. Lấy log mới nhất của từng mission. Nếu `latest_log.timestamp < now - 10s`, kiểm tra xem đã có `MaintenanceAlert` pending cho mission này chưa (dedup). Nếu chưa, tạo `Incident HIGH` và `MaintenanceAlert` với title "Telemetry signal lost (>10s)", gửi notification cho tất cả Operator và Admin kèm last known coordinates.

---

### 3.4.3 SD — In-flight Incident Handling & Emergency Landing

> [emergency-land.html](.archify/seq-emergency-land-20261112-090000/emergency-land.html)

**Path A — Operator kích hoạt Emergency Land:** Operator gọi `POST /operator/missions/{id}/emergency-land` với `reason`. Router kiểm tra mission đang ở trạng thái active (FLYING/IN_PROGRESS/ACTIVE_MISSION/APPROVED) — nếu không thì 400. Chuyển mission sang `INCIDENT_RETURN`, set `handling_status=CANNOT_CONTINUE`, ghi `arrived_at=now`, clear `arrival_confirmed_at`. INSERT `Incident` với `severity=HIGH`, `status=OPEN`, `requires_technical_inspection=True`, description kèm reason. Sau `db.flush()` lấy `incident.id`, INSERT `MaintenanceAlert` với `source=EmergencyLand`. Flush lại lấy `alert.id`. UPDATE drone sang `MAINTENANCE`. Gửi SYSTEM_ALERT notification cho tất cả Operator, và MISSION_STATUS notification nhẹ hơn cho Customer. Response `200 EmergencyLandResponse` với mission_id, previous_status, incident_id, alert_id.

**Path B — Simulator tự trigger incident (FAILSAFE_RTH):** Trong vòng lặp bay, sau mỗi waypoint, Simulator kiểm tra xem có armed incident chưa đến bước trigger hay random incident xảy ra không. Nếu có và loại là `FAILSAFE_RTH`, `_trigger_incident()` được gọi — nó UPDATE mission sang `INCIDENT_RETURN` trong DB, INSERT Incident, MaintenanceAlert và gửi notification. Sau đó Simulator tính `_find_nearest_active_hub()` từ tọa độ sự cố, nội suy waypoints từ đó về hub gần nhất, và gọi `_fly_incident_return()` — tiếp tục insert TelemetryLog theo từng waypoint trên đường về. Khi đến hub, UPDATE `handling_status=AT_HUB`, `arrived_at=now`, cập nhật `drone.current_hub_id`, và gửi notification cho Technician tại hub đó rằng drone đã về cần kiểm tra.

Nếu loại incident là `SIGNAL_LOSS`, Simulator đơn giản dừng vòng lặp — không insert gì thêm. SignalLossWatcher sẽ phát hiện gap > 10s và tạo incident. Nếu là `DISCONNECT_CRASH`, Simulator gọi `_trigger_disconnect_crash()` — insert một TelemetryLog duy nhất tại tọa độ rơi với altitude=0 và speed=0, chuyển mission sang `FAILED`, drone sang `MAINTENANCE`, và tạo incident HIGH với mô tả crash.

---

*Generated from source code at commit `7b63ec3c99e7f74462bd6023266ffdf42088a896` · November 2026*
