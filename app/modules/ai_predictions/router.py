from fastapi import APIRouter

router = APIRouter(prefix="/predictions", tags=["AI Predictions"])

@router.post("/energy")
def predict_energy():
    return {"message": "Energy prediction logic using SHAP and Regression models"}
