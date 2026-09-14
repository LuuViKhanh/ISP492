from fastapi import APIRouter, Depends
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole

router = APIRouter(prefix="/missions", tags=["Missions"])

allow_operator_and_customer = RoleChecker([UserRole.OPERATOR, UserRole.CUSTOMER])
allow_operator = RoleChecker([UserRole.OPERATOR])

@router.post("/request")
def create_mission_request(user: CurrentUser = Depends(allow_operator_and_customer)):
    return {"message": "Mission request created."}

@router.get("/")
def get_missions(user: CurrentUser = Depends(get_current_user)):
    return {"message": f"Missions list for user {user.username}"}
