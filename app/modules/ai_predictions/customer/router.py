from fastapi import APIRouter

router = APIRouter(
    prefix="/customer/ai_predictions",
    tags=["AI Predictions"]
)

@router.get("/")
def get_customer_ai_predictions():
    return {"message": "This is customer endpoint for ai_predictions"}
