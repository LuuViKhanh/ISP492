from fastapi import APIRouter

router = APIRouter(
    prefix="/technician/missions",
    tags=["Technician - Missions"]
)

@router.get("/")
def get_technician_missions():
    return {"message": "This is technician endpoint for missions"}
