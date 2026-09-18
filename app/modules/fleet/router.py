from fastapi import APIRouter, Depends
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.technician.router import router as technician_router

router = APIRouter(prefix="/fleet", tags=["Fleet Management"])

router.include_router(technician_router)

allow_technician = RoleChecker([UserRole.TECHNICIAN])

@router.get("/drones")
def get_drones(user: CurrentUser = Depends(allow_technician)):
    return {"message": "Drones list"}
