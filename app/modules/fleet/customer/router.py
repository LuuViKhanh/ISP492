from fastapi import APIRouter

router = APIRouter(
    prefix="/customer/fleet",
    tags=["Fleet"]
)

@router.get("/")
def get_customer_fleet():
    return {"message": "This is customer endpoint for fleet"}
