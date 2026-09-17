from fastapi import APIRouter

router = APIRouter(
    prefix="/operator/missions",
    tags=["Operator - Missions"]
)

@router.get("/")
def get_operator_missions():
    return {"message": "This is operator endpoint for missions"}
