from fastapi import APIRouter

router = APIRouter(
    prefix="/admin/auth",
    tags=["Admin - Auth"]
)

@router.get("/")
def get_admin_auth():
    return {"message": "This is admin endpoint for auth"}
