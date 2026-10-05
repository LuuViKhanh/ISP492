from fastapi import APIRouter

router = APIRouter(
    prefix="/technician/auth",
    tags=["Authentication"]
)

@router.get("/")
def get_technician_auth():
    return {"message": "This is technician endpoint for auth"}
