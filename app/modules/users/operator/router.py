from fastapi import APIRouter

router = APIRouter(
    prefix="/operator/users",
    tags=["Operator - Users"]
)

@router.get("/")
def get_operator_users():
    return {"message": "This is operator endpoint for users"}
