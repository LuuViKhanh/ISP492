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

from app.core.config import settings
from app.database.db import async_engine, Base
import app.modules.auth.models  # noqa: F401
import app.modules.fleet.models  # noqa: F401
import app.modules.missions.models  # noqa: F401
import app.modules.system.models  # noqa: F401

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
from app.modules.payments.router import router as payments_router
from app.modules.chatbot.rag.router import router as rag_router

# Developer Database API (dùng chung server, prefix /dev/db/...)
from app.database.router import router as db_router

import asyncio
from app.database.db import async_engine, Base
from app.database.cron_db import run_with_retry
from app.modules.fleet.technician.service import run_maintenance_alerts_logic, run_battery_overdue_alerts_logic
from app.shared.workers import run_approval_deadline_watcher, run_telemetry_signal_loss_watcher, run_battery_charging_simulation, run_pickup_reminder_watcher

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
        await asyncio.sleep(3600)

async def run_realtime_cronjobs():
    """Vòng lặp chạy ngầm mỗi 3s cho realtime monitoring.
    FDE-193: mỗi worker nhận session độc lập qua run_with_retry.
    FDE-194: retry với max_retries=2 và backoff=1s (realtime cần nhanh hơn).
    """
    while True:
        await run_with_retry(run_approval_deadline_watcher,    "approval_deadline",    max_retries=2, base_backoff=1.0)
        await run_with_retry(run_telemetry_signal_loss_watcher, "telemetry_signal_loss", max_retries=2, base_backoff=1.0)
        await asyncio.sleep(3)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Bắt đầu cronjob ngầm
    task_hourly = asyncio.create_task(run_hourly_cronjobs())
    task_realtime = asyncio.create_task(run_realtime_cronjobs())
    
    yield
    
    # Hủy cronjob khi tắt server
    task_hourly.cancel()
    task_realtime.cancel()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
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
                
                role_icons = {
                    "ADMIN": "🔴",
                    "OPERATOR": "🔵",
                    "CUSTOMER": "🟢",
                    "TECHNICIAN": "🟠",
                    "NOTIFICATIONS": "🟣"
                }
                
                # Auto-prefix Summary
                if detected_role:
                    icon = role_icons.get(detected_role, "⚪")
                    prefix = f"{icon} [{detected_role}]"
                    original_summary = operation.get("summary", "")
                    
                    if original_summary:
                        # Prevent double prefixing if it somehow runs multiple times
                        if not original_summary.startswith(icon) and not original_summary.startswith(f"[{detected_role}]"):
                            operation["summary"] = f"{prefix} {original_summary}"
                        elif original_summary.startswith(f"[{detected_role}]"):
                            # If it only has the text prefix, add the icon
                            operation["summary"] = f"{icon} {original_summary}"
                    else:
                        operation["summary"] = f"{prefix} {operation.get('operationId', '')}"
                        
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    # Chấp nhận mọi domain (localhost bất kỳ port nào, hoặc domain production)
    # Cơ chế: FastAPI sẽ đọc header Origin của request và trả về đúng Origin đó 
    # thay vì trả về "*" (giúp vượt qua lỗi block của browser khi allow_credentials=True).
    allow_origin_regex=r"^https?://.*$",
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
app.include_router(payments_router, prefix=settings.API_V1_STR)
app.include_router(rag_router, prefix=settings.API_V1_STR)
app.include_router(db_router)

# Tự động include các router của các roles
import importlib
loaded_chatbot_roles = []
for mod in ["auth", "users", "missions", "fleet", "ai_predictions", "orders", "chatbot", "dashboard"]:
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
                print(f"⚠️ [IMPORT ERROR] Không thể nạp '{target_mod}' do lỗi: {e}")
        except Exception as e:
            print(f"[WARN] Failed to load {target_mod}: {e}")

if loaded_chatbot_roles:
    print(f"🤖 [CHATBOT] Đã nạp thành công {len(loaded_chatbot_roles)}/4 Chatbot Routers: {', '.join(loaded_chatbot_roles)}")



@app.get("/")
def root():
    return {
        "message": "Welcome to DroneOptAI API Gateway"
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )