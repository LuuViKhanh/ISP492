from fastapi import APIRouter

router = APIRouter(
    prefix="/customer/missions",
    tags=["Customer - Missions"]
)

@router.get("/")
def get_customer_missions():
    return {"message": "This is customer endpoint for missions"}
