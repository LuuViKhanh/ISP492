from fastapi import APIRouter

router = APIRouter(
    prefix="/technician/users",
    tags=["Users"]
)

@router.get("/")
def get_technician_users():
    return {"message": "This is technician endpoint for users"}
