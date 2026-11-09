"""
system/service.py — Logging helpers

Hai loại log:
  log_activity()     → activity_logs  (user-triggered: login, create order, approve...)
  log_system_event() → system_logs    (automated: cron workers, errors, startup...)

Backward-compat:
  log_action()       → gọi log_activity() với signature cũ
"""

import json
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Activity Log ──────────────────────────────────────────────────

async def log_activity(
    db: AsyncSession,
    action: str,                          # LOGIN / CREATE_ORDER / APPROVE_REQUEST / ...
    user_id: Optional[Any] = None,
    user_email: Optional[str] = None,
    role: Optional[str] = None,
    resource_type: Optional[str] = None,  # order / mission / drone / user / ...
    resource_id: Optional[str] = None,
    description: Optional[str] = None,
    status: str = "success",              # success / failed
    details: Optional[dict] = None,
) -> None:
    """
    Ghi một hành động của user vào activity_logs.

    Dùng cho:
      - Auth: LOGIN, LOGOUT, REGISTER, PASSWORD_RESET
      - Orders: CREATE_ORDER, CANCEL_ORDER
      - Missions: CREATE_MISSION, UPDATE_MISSION_STATUS, EMERGENCY_LAND
      - Fleet: CONFIRM_ARRIVAL, WORK_ORDER_COMPLETE, MAINTENANCE_ACK
      - Inventory: PARTS_REQUEST, STOCK_RECEIVE, STOCK_TRANSFER
      - Admin: USER_CREATE, HUB_UPDATE, ...
    """
    try:
        await db.execute(text("""
            INSERT INTO activity_logs
                (user_id, user_email, role, action, resource_type, resource_id,
                 description, status, details_json, created_at)
            VALUES
                (:user_id, :user_email, :role, :action, :resource_type, :resource_id,
                 :description, :status, CAST(:details_json AS jsonb), now())
        """), {
            "user_id":      str(user_id) if user_id else None,
            "user_email":   user_email,
            "role":         role,
            "action":       action,
            "resource_type": resource_type,
            "resource_id":  str(resource_id) if resource_id else None,
            "description":  description,
            "status":       status,
            "details_json": json.dumps(details) if details else None,
        })
        await db.commit()
    except Exception:
        # Log không được làm crash request
        pass


# ── System Log ────────────────────────────────────────────────────

async def log_system_event(
    db: AsyncSession,
    source: str,          # worker name / module: "run_critical_order_notifier" / "startup"
    event: str,           # WORKER_RUN / MISSION_AUTO_CANCEL / ERROR / STARTUP / ...
    message: str,
    level: str = "INFO",  # INFO / WARNING / ERROR / CRITICAL
    details: Optional[dict] = None,
) -> None:
    """
    Ghi sự kiện tự động của hệ thống vào system_logs.

    Dùng cho:
      - Workers: kết quả mỗi lần chạy, số records xử lý
      - Cron errors: lỗi không mong đợi trong cron loop
      - Server events: startup, shutdown
    """
    try:
        await db.execute(text("""
            INSERT INTO system_logs (source, level, event, message, details_json, created_at)
            VALUES (:source, :level, :event, :message, CAST(:details_json AS jsonb), now())
        """), {
            "source":       source,
            "level":        level,
            "event":        event,
            "message":      message,
            "details_json": json.dumps(details) if details else None,
        })
        await db.commit()
    except Exception:
        pass


# ── Backward compat ───────────────────────────────────────────────

async def log_action(
    db: AsyncSession,
    user_id: Any,
    action: str,
    resource_table: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Backward-compatible wrapper → gọi log_activity()."""
    await log_activity(
        db=db,
        action=action,
        user_id=user_id,
        resource_type=resource_table,
        details=details,
    )


# ── Query helpers ─────────────────────────────────────────────────

async def get_activity_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    role: Optional[str] = None,
    resource_type: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    offset = (page - 1) * page_size
    filters, params = [], {"limit": page_size, "offset": offset}

    if user_id:
        filters.append("(user_id = :user_id OR user_email ILIKE :user_email_like)")
        params["user_id"] = user_id
        params["user_email_like"] = f"%{user_id}%"
    if action:
        filters.append("action ILIKE :action")
        params["action"] = f"%{action}%"
    if role:
        filters.append("role = :role")
        params["role"] = role
    if resource_type:
        filters.append("resource_type ILIKE :resource_type")
        params["resource_type"] = f"%{resource_type}%"
    if status:
        filters.append("status = :status")
        params["status"] = status

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    total = (await db.execute(
        text(f"SELECT COUNT(*) FROM activity_logs {where}"), params
    )).scalar()

    rows = (await db.execute(text(
        f"SELECT id, user_id, user_email, role, action, resource_type, resource_id, "
        f"description, status, details_json, created_at "
        f"FROM activity_logs {where} "
        f"ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    ), params)).fetchall()

    keys = ["id", "user_id", "user_email", "role", "action", "resource_type",
            "resource_id", "description", "status", "details_json", "created_at"]
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [dict(zip(keys, r)) for r in rows],
    }


async def get_system_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 50,
    source: Optional[str] = None,
    level: Optional[str] = None,
    event: Optional[str] = None,
) -> dict:
    offset = (page - 1) * page_size
    filters, params = [], {"limit": page_size, "offset": offset}

    if source:
        filters.append("source ILIKE :source")
        params["source"] = f"%{source}%"
    if level:
        filters.append("level = :level")
        params["level"] = level.upper()
    if event:
        filters.append("event ILIKE :event")
        params["event"] = f"%{event}%"

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    total = (await db.execute(
        text(f"SELECT COUNT(*) FROM system_logs {where}"), params
    )).scalar()

    rows = (await db.execute(text(
        f"SELECT id, source, level, event, message, details_json, created_at "
        f"FROM system_logs {where} "
        f"ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    ), params)).fetchall()

    keys = ["id", "source", "level", "event", "message", "details_json", "created_at"]
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [dict(zip(keys, r)) for r in rows],
    }


# ── Legacy get_audit_logs (backward compat) ───────────────────────

async def get_audit_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_table: Optional[str] = None,
) -> dict:
    """Backward-compat: đọc từ activity_logs."""
    return await get_activity_logs(
        db, page, page_size,
        user_id=user_id,
        action=action,
        resource_type=resource_table,
    )


# ── Quick-log helpers (dùng trong router mà không cần await riêng) ───────────

def _build_activity_log(
    action: str,
    user_id: str | None = None,
    user_email: str | None = None,
    role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    description: str | None = None,
    status: str = "success",
    details: dict | None = None,
):
    """
    Tạo Notification-style activity log entry — dùng khi cần fire-and-forget
    mà không muốn thêm await vào giữa business logic.

    Pattern sử dụng:
        try:
            entry = _build_activity_log(...)
            db.add(entry)
        except Exception:
            pass
    """
    import json
    from datetime import timezone as _tz
    from app.modules.system.models import AuditLog  # fallback đến bảng cũ để dùng ngay
    return {
        "user_id": str(user_id) if user_id else None,
        "user_email": user_email,
        "role": role,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None,
        "description": description,
        "status": status,
        "details_json": details,
        "created_at": datetime.now(_tz.utc),
    }
