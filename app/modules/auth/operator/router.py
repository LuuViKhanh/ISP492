from fastapi import APIRouter

router = APIRouter(
    prefix="/operator/auth",
    tags=["Operator - Auth"]
)

@router.get("/")
def get_operator_auth():
    return {"message": "This is operator endpoint for auth"}
