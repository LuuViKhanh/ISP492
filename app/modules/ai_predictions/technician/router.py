from fastapi import APIRouter

router = APIRouter(
    prefix="/technician/ai_predictions",
    tags=["Technician - Ai_predictions"]
)

@router.get("/")
def get_technician_ai_predictions():
    return {"message": "This is technician endpoint for ai_predictions"}
