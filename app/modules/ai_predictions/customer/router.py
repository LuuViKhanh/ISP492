from fastapi import APIRouter

router = APIRouter(
    prefix="/customer/ai_predictions",
    tags=["Customer - Ai_predictions"]
)

@router.get("/")
def get_customer_ai_predictions():
    return {"message": "This is customer endpoint for ai_predictions"}
