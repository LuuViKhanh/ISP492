from fastapi import APIRouter

router = APIRouter(
    prefix="/technician/fleet",
    tags=["Technician - Fleet"]
)

@router.get("/")
def get_technician_fleet():
    return {"message": "This is technician endpoint for fleet"}
