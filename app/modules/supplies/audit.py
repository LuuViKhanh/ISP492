"""
Ghi activity log cho các thao tác Supplies (DEF-04).

Quy tắc:
- Chỉ gọi SAU khi endpoint đã commit thành công.
- Lỗi ghi log không được làm hỏng request: mọi exception bị nuốt và in ra console.
"""
from typing import Any, Optional

from app.modules.system.service import log_activity


async def audit_supply_action(
    db,
    user,
    action: str,
    resource_id: Any,
    description: str,
    details: Optional[dict] = None,
    resource_type: str = "supplies",
) -> None:
    try:
        await log_activity(
            db,
            action=action,
            user_id=user.id,
            user_email=getattr(user, "email", None),
            role=user.role.value if hasattr(user.role, "value") else str(user.role),
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            description=description,
            details=details,
        )
    except Exception as e:
        print(f"[AUDIT] Không ghi được activity log cho {action}: {e}")
