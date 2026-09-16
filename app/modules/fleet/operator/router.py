from fastapi import APIRouter

router = APIRouter(
    prefix="/operator/fleet",
    tags=["Operator - Fleet"]
)

@router.get("/")
def get_operator_fleet():
    return {"message": "This is operator endpoint for fleet"}
