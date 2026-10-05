from fastapi import APIRouter

router = APIRouter(
    prefix="/operator/auth",
    tags=["Authentication"]
)

@router.get("/")
def get_operator_auth():
    return {"message": "This is operator endpoint for auth"}
