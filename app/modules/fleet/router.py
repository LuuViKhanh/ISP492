from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.admin.router import (
    supply_batteries,
    BatterySupplyRequest,
    BatterySupplyResponse,
    allow_admin,
)

router = APIRouter(prefix="/fleet", tags=["Fleet"])

allow_technician = RoleChecker([UserRole.TECHNICIAN])

@router.get("/drones")
def get_drones(user: CurrentUser = Depends(allow_technician)):
    return {"message": "Drones list"}


@router.post("/admin/batteries/supply", response_model=BatterySupplyResponse, summary="Allocate batteries from Admin warehouse to Hub (Route Alias)")
async def supply_batteries_alias(
    body: BatterySupplyRequest,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await supply_batteries(body=body, user=user, db=db)
