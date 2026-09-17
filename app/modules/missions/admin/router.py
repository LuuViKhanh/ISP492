from fastapi import APIRouter

router = APIRouter(
    prefix="/admin/missions",
    tags=["Admin - Missions"]
)

@router.get("/")
def get_admin_missions():
    return {"message": "This is admin endpoint for missions"}
