from fastapi import APIRouter, Depends
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole

router = APIRouter(prefix="/fleet", tags=["Fleet Management"])

allow_technician = RoleChecker([UserRole.TECHNICIAN])

@router.get("/drones")
def get_drones(user: CurrentUser = Depends(allow_technician)):
    return {"message": "Drones list"}
