from fastapi import APIRouter

router = APIRouter(
    prefix="/admin/auth",
    tags=["Authentication"]
)

@router.get("/")
def get_admin_auth():
    return {"message": "This is admin endpoint for auth"}
