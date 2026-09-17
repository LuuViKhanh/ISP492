from fastapi import APIRouter

router = APIRouter(
    prefix="/admin/ai_predictions",
    tags=["Admin - Ai_predictions"]
)

@router.get("/")
def get_admin_ai_predictions():
    return {"message": "This is admin endpoint for ai_predictions"}
