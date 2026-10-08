from fastapi import APIRouter

router = APIRouter(
    prefix="/admin/ai_predictions",
    tags=["AI Predictions"]
)

@router.get("/")
def get_admin_ai_predictions():
    return {"message": "This is admin endpoint for ai_predictions"}
