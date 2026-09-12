"""API layer: HTTP route only. Wires the request to the service and returns a response."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.apis.GetListUser.repository import GetListUserRepository
from app.apis.GetListUser.schema import GetListUserResponse
from app.apis.GetListUser.service import GetListUserService
from app.common.database import get_db
from app.common.response import ApiResponse, success_response

router = APIRouter(prefix="/users", tags=["Users"])


def get_service(db: Session = Depends(get_db)) -> GetListUserService:
    return GetListUserService(repository=GetListUserRepository(db))


@router.get("", response_model=ApiResponse[GetListUserResponse])
def get_list_user(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    service: GetListUserService = Depends(get_service),
):
    result = service.get_list_user(page=page, page_size=page_size)
    return success_response(data=result)
