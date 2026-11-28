import os
import sys

# Đảm bảo mã hóa UTF-8 trên Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Thêm thư mục gốc "project" vào Python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import traceback

from app.core.config import settings
from app.database.db import async_engine, Base
import app.modules.auth.models  # noqa: F401
import app.modules.fleet.models  # noqa: F401
import app.modules.missions.models  # noqa: F401 — includes DeliveryRating
import app.modules.system.models  # noqa: F401
import app.modules.supplies.models   # noqa: F401

# Import routers từ các module
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.missions.router import router as missions_router
from app.modules.fleet.router import router as fleet_router
from app.modules.ai_predictions.router import router as ai_router

from app.modules.system.router import router as system_router
from app.modules.system.admin.router import router as system_admin_router
from app.modules.system.notifications.router import router as notifications_router
from app.modules.orders.router import router as orders_router
from app.modules.orders.customer.addresses import router as addresses_router
from app.modules.payments.router import router as payments_router
from app.modules.chatbot.rag.router import router as rag_router
from app.modules.reports.router import router as reports_router
from app.shared.image_storage import (
    router as uploads_router,
    set_request_base_url,
    reset_request_base_url,
)

# Developer Database API (dùng chung server, prefix /dev/db/...)
from app.database.router import router as db_router

import asyncio
from app.database.cron_db import run_with_retry
from app.modules.fleet.technician.service import run_maintenance_alerts_logic, run_battery_overdue_alerts_logic, run_preventive_maintenance_scheduler, run_drone_daily_stats
from app.shared.workers import run_approval_deadline_watcher, run_telemetry_signal_loss_watcher, run_battery_charging_simulation, run_pickup_reminder_watcher, run_aging_priority_watcher, run_critical_order_notifier, run_deadline_alert_watcher

async def run_hourly_cronjobs():
    """Vòng lặp chạy ngầm mỗi tiếng (3600s).
    FDE-193: mỗi worker nhận session độc lập qua run_with_retry.
    FDE-194: tự động retry khi gặp lỗi tạm thời (connection/timeout).
    """
    while True:
        await run_with_retry(run_maintenance_alerts_logic,   "maintenance_alerts")
        await run_with_retry(run_battery_overdue_alerts_logic, "battery_overdue_alerts")
        await run_with_retry(run_battery_charging_simulation,  "battery_charging_sim")
        await run_with_retry(run_pickup_reminder_watcher,      "pickup_reminder")
        await run_with_retry(run_critical_order_notifier,      "critical_order_notifier")
        await run_with_retry(run_preventive_maintenance_scheduler, "preventive_maintenance")
        await run_with_retry(run_drone_daily_stats, "drone_daily_stats")
        await asyncio.sleep(3600)

async def run_quarter_cronjobs():
    """Vòng lặp chạy ngầm mỗi 15 phút (900s).
    Dùng cho các worker cần tần suất thấp hơn realtime nhưng cao hơn hourly.
    """
    while True:
        await run_with_retry(run_deadline_alert_watcher, "deadline_alert")
        await run_with_retry(run_aging_priority_watcher, "aging_priority")
        await asyncio.sleep(900)

async def run_realtime_cronjobs():
    """Vòng lặp chạy ngầm mỗi 3s cho realtime monitoring.
    FDE-193: mỗi worker nhận session độc lập qua run_with_retry.
    FDE-194: retry với max_retries=2 và backoff=1s (realtime cần nhanh hơn).
    [PERF NOTE] Tắt log_on_success cho vòng lặp 3s để tránh ghi 57,600 dòng system_logs/ngày; lỗi vẫn ghi đầy đủ.
    run_approval_deadline_watcher chạy mỗi 15s (5 ticks) thay vì mỗi 3s.
    """
    tick = 0
    while True:
        if tick % 5 == 0:
            await run_with_retry(
                run_approval_deadline_watcher, "approval_deadline",
                max_retries=2, base_backoff=1.0, log_on_success=False,
            )
        await run_with_retry(
            run_telemetry_signal_loss_watcher, "telemetry_signal_loss",
            max_retries=2, base_backoff=1.0, log_on_success=False,
        )
        tick = (tick + 1) % 5
        await asyncio.sleep(3)

_WARMUP_STATE: dict = {
    "ready": False,
    "warmup_sec": None,
    "hubs": 0,
    "weather_cells": 0,
}


async def _warmup_caches_background():
    """
    [PERF NOTE] Nạp ấm (warm-up) các cache RAM và connection pool DB ngay khi server vừa khởi động
    và định kỳ làm mới mỗi 4 phút (240s < TTL 300s của Hubs/Compat cache) ở chế độ nền (non-blocking)
    để lượt bấm đầu tiên của Operator / Hội đồng luôn đạt tốc độ warm mà không cần bấm mồi trước:
    1. Bắt tay SSL + nạp 18 ENUM codecs cho 5 kết nối Supabase pool + cache Hubs & Battery Compatibility.
    2. Load sẵn model ML predict_flight_energy vào RAM.
    3. Fetch song song thời tiết Open-Meteo theo lưới 0.1° cho cả tọa độ Hub lẫn midpoint giữa các Hub.
    """
    import time
    from sqlalchemy import text
    from app.database.db import AsyncSessionLocal
    from app.modules.missions.bundle_service import (
        get_all_hubs_cached,
        get_compatible_battery_models_map,
        predict_flight_energy_cached,
    )
    from app.shared.weather import fetch_weather, _weather_grid_key

    async def _warm_extra_pool_conn():
        async with AsyncSessionLocal() as extra_db:
            await extra_db.execute(text("SELECT 1"))

    first_run = True
    while True:
        t0 = time.perf_counter()
        try:
            hubs = []
            async with AsyncSessionLocal() as db:
                hubs = await get_all_hubs_cached(db)
                await get_compatible_battery_models_map(db)

            if first_run:
                await asyncio.gather(
                    _warm_extra_pool_conn(),
                    _warm_extra_pool_conn(),
                    _warm_extra_pool_conn(),
                    _warm_extra_pool_conn(),
                    asyncio.to_thread(predict_flight_energy_cached, 10.0, 1.0),
                    return_exceptions=True,
                )

            valid_hubs = [h for h in hubs if h.latitude is not None and h.longitude is not None]
            unique_cells: set[tuple[float, float]] = set()
            for h in valid_hubs:
                unique_cells.add(_weather_grid_key(h.latitude, h.longitude))
            for i in range(len(valid_hubs)):
                for j in range(i + 1, len(valid_hubs)):
                    mlat = (float(valid_hubs[i].latitude) + float(valid_hubs[j].latitude)) / 2.0
                    mlng = (float(valid_hubs[i].longitude) + float(valid_hubs[j].longitude)) / 2.0
                    unique_cells.add(_weather_grid_key(mlat, mlng))

            if unique_cells:
                sem = asyncio.Semaphore(4)

                async def _warm_one(lat: float, lng: float):
                    async with sem:
                        await fetch_weather(lat, lng, use_cache=False)

                await asyncio.gather(
                    *(_warm_one(lat, lng) for lat, lng in unique_cells),
                    return_exceptions=True,
                )

            if first_run:
                elapsed = round(time.perf_counter() - t0, 2)
                _WARMUP_STATE["ready"] = True
                _WARMUP_STATE["warmup_sec"] = elapsed
                _WARMUP_STATE["hubs"] = len(valid_hubs)
                _WARMUP_STATE["weather_cells"] = len(unique_cells)
                first_run = False
                print(
                    f"[STARTUP WARMUP] Ready in {elapsed}s "
                    f"(pool=5, hubs={len(valid_hubs)}, weather_cells={len(unique_cells)})"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[STARTUP WARMUP WARNING] Không thể warm-up cache nền: {exc}")
            if first_run:
                _WARMUP_STATE["ready"] = True
                _WARMUP_STATE["warmup_sec"] = round(time.perf_counter() - t0, 2)
                first_run = False

        await asyncio.sleep(240)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        print(f"[STARTUP DB WARNING] Không thể chạy create_all lúc khởi động (bỏ qua để giữ Server online): {e}")
        
    # [PERF NOTE] Hệ thống dùng in-memory cache (Hubs, Compat, Route, Weather) cho FastAPI 1 worker;
    # không bật nhiều workers (--workers > 1) trừ khi chuyển cache sang Redis.
    task_hourly = asyncio.create_task(run_hourly_cronjobs())
    task_quarter = asyncio.create_task(run_quarter_cronjobs())
    task_realtime = asyncio.create_task(run_realtime_cronjobs())
    task_warmup = asyncio.create_task(_warmup_caches_background())
    
    yield
    
    # Hủy cronjob khi tắt server
    task_hourly.cancel()
    task_quarter.cancel()
    task_realtime.cancel()
    task_warmup.cancel()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
    swagger_ui_parameters={
        "filter": True,                  # Bật thanh tìm kiếm/lọc nhanh API ngay đầu /docs
        "persistAuthorization": True,    # Giữ nguyên JWT Bearer token khi F5 lại /docs
        "docExpansion": "none",          # Thu gọn các tag mặc định giúp dễ tìm & load nhanh
        "displayRequestDuration": True,  # Hiển thị thời gian phản hồi (ms) cho mỗi request
    },
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version="1.0.0", routes=app.routes)
    
    if "components" not in schema:
        schema["components"] = {}
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    
    roles_set = {"admin", "operator", "customer", "technician", "notifications"}
    
    for path_key, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict):
                operation["security"] = [{"BearerAuth": []}]
                
                # Detect Role from path
                parts = path_key.strip("/").split("/")
                detected_role = None
                for part in parts:
                    if part.lower() in roles_set:
                        detected_role = part.upper()
                        break
                
                # Auto-prefix Summary
                if detected_role:
                    prefix = f"[{detected_role}]"
                    original_summary = operation.get("summary", "")

                    if original_summary:
                        if not original_summary.startswith(prefix):
                            operation["summary"] = f"{prefix} {original_summary}"
                    else:
                        operation["summary"] = f"{prefix} {operation.get('operationId', '')}"
                        
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

# Bật ProxyHeadersMiddleware để giữ nguyên HTTPS và Host từ Cloudflare Tunnel / Reverse Proxy
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


class PublicBaseURLMiddleware:
    """Pure ASGI middleware lưu public base URL vào ContextVar (không buffer stream)."""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        proto_raw = headers.get(b"x-forwarded-proto")
        proto = proto_raw.decode("latin1").split(",")[0].strip() if proto_raw else (scope.get("scheme") or "http")
        host_raw = headers.get(b"x-forwarded-host") or headers.get(b"host")
        host = host_raw.decode("latin1").split(",")[0].strip() if host_raw else ""
        base = f"{proto}://{host}" if host else ""
        token = set_request_base_url(base)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_base_url(token)


app.add_middleware(PublicBaseURLMiddleware)

# [PERF NOTE] Nén JSON payload >= 1KB (GZip) để giảm dung lượng chiều upload từ laptop server qua Cloudflare Tunnel.
# Đặt trước CORSMiddleware để CORS headers luôn được áp dụng đầy đủ.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Bắt lỗi toàn cục để log chi tiết lỗi ra console thay vì trả 500 rỗng
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "error_message": str(exc),
            "path": str(request.url)
        }
    )

app.add_middleware(
    CORSMiddleware,
    # Chấp nhận mọi domain (localhost bất kỳ port nào, hoặc domain production)
    # Cơ chế: FastAPI sẽ đọc header Origin của request và trả về đúng Origin đó 
    # thay vì trả về "*" (giúp vượt qua lỗi block của browser khi allow_credentials=True).
    allow_origin_regex=r".*",
    expose_headers=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(users_router, prefix=settings.API_V1_STR)
app.include_router(missions_router, prefix=settings.API_V1_STR)
app.include_router(fleet_router, prefix=settings.API_V1_STR)
app.include_router(ai_router, prefix=settings.API_V1_STR)
app.include_router(system_router, prefix=settings.API_V1_STR)
app.include_router(system_admin_router, prefix=settings.API_V1_STR)
app.include_router(notifications_router, prefix=settings.API_V1_STR)
app.include_router(orders_router, prefix=settings.API_V1_STR)
app.include_router(addresses_router, prefix=settings.API_V1_STR)
app.include_router(payments_router, prefix=settings.API_V1_STR)
app.include_router(rag_router, prefix=settings.API_V1_STR)
app.include_router(reports_router, prefix=settings.API_V1_STR)
app.include_router(uploads_router, prefix=settings.API_V1_STR)
if os.getenv("ENABLE_DEV_DB_ROUTER", "").lower() == "true":
    app.include_router(db_router)

# Tự động include các router của các roles
import importlib
loaded_chatbot_roles = []
for mod in ["auth", "users", "missions", "fleet", "ai_predictions", "orders", "chatbot", "dashboard", "supplies"]:
    for role in ["admin", "operator", "technician", "customer"]:
        target_mod = f"app.modules.{mod}.{role}.router"
        try:
            router_mod = importlib.import_module(target_mod)
            app.include_router(router_mod.router, prefix=settings.API_V1_STR)
            if mod == "chatbot":
                loaded_chatbot_roles.append(role.capitalize())
        except ModuleNotFoundError as e:
            missing_mod = getattr(e, "name", "") or ""
            # Nếu bản thân subpackage/router.py của role này không tồn tại -> bỏ qua bình thường
            if missing_mod and target_mod.startswith(missing_mod):
                pass
            else:
                # Nếu file router.py CÓ TỒN TẠI nhưng bị thiếu thư viện bên trong -> Cảnh báo rõ ràng cho dev!
                print(f"[IMPORT ERROR] Không thể nạp '{target_mod}' do lỗi: {e}")
        except Exception as e:
            print(f"[WARN] Failed to load {target_mod}: {e}")

if loaded_chatbot_roles:
    print(f"[CHATBOT] Đã nạp thành công {len(loaded_chatbot_roles)}/4 Chatbot Routers: {', '.join(loaded_chatbot_roles)}")



@app.get("/")
def root():
    return {
        "message": "Welcome to DroneOptAI API Gateway"
    }


@app.get("/health/ready")
def health_ready():
    return _WARMUP_STATE


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )