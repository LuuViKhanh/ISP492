from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database.db import get_async_db
from app.modules.system.schemas import AuditLogListResponse
from app.modules.system import service
from app.shared.dependencies import RoleChecker
from app.shared.roles import UserRole

router = APIRouter(prefix="/system/audit-logs", tags=["System - Admin"])

require_admin = RoleChecker([UserRole.ADMIN])


@router.get("", response_model=AuditLogListResponse, dependencies=[Depends(require_admin)])
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    resource_table: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    return await service.get_audit_logs(db, page, page_size, user_id, action, resource_table)
