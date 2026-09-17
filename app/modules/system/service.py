from typing import Optional, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def log_action(
    db: AsyncSession,
    user_id: Any,
    action: str,
    resource_table: Optional[str] = None,
    details: Optional[dict] = None,
):
    """Ghi một audit log entry. Gọi hàm này sau mỗi hành động quan trọng."""
    import json
    await db.execute(text(
        "INSERT INTO public.audit_logs (user_id, action, resource_table, details_json, created_at) "
        "VALUES (:user_id, :action::audit_logs_action, :resource_table, :details_json, now())"
    ), {
        "user_id": user_id,
        "action": action,
        "resource_table": resource_table,
        "details_json": json.dumps(details) if details else None,
    })
    await db.commit()


async def get_audit_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_table: Optional[str] = None,
) -> dict:
    offset = (page - 1) * page_size

    filters = []
    params: dict = {"limit": page_size, "offset": offset}

    if user_id:
        filters.append("user_id::varchar = :user_id")
        params["user_id"] = user_id
    if action:
        filters.append("action::varchar ILIKE :action")
        params["action"] = f"%{action}%"
    if resource_table:
        filters.append("resource_table ILIKE :resource_table")
        params["resource_table"] = f"%{resource_table}%"

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    count_result = await db.execute(text(f"SELECT COUNT(*) FROM public.audit_logs {where}"), params)
    total = count_result.scalar()

    result = await db.execute(text(
        f"SELECT id, user_id, action::varchar, resource_table, details_json, created_at "
        f"FROM public.audit_logs {where} "
        f"ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    ), params)

    rows = result.fetchall()
    keys = result.keys()
    items = [dict(zip(keys, row)) for row in rows]

    return {"total": total, "page": page, "page_size": page_size, "items": items}
