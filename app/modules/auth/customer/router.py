from fastapi import APIRouter

router = APIRouter(
    prefix="/customer/auth",
    tags=["Customer - Auth"]
)

@router.get("/")
def get_customer_auth():
    return {"message": "This is customer endpoint for auth"}
