from fastapi import APIRouter

router = APIRouter(
    prefix="/admin/fleet",
    tags=["Admin - Fleet"]
)

@router.get("/")
def get_admin_fleet():
    return {"message": "This is admin endpoint for fleet"}
