"""Business logic layer: validation, orchestration, calls the repository."""
from app.apis.GetListUser.repository import GetListUserRepository
from app.apis.GetListUser.schema import GetListUserResponse, UserItem
from app.common.logger import get_logger

logger = get_logger(__name__)


class GetListUserService:
    def __init__(self, repository: GetListUserRepository):
        self.repository = repository

    def get_list_user(self, page: int, page_size: int) -> GetListUserResponse:
        skip = (page - 1) * page_size
        total = self.repository.count_all()
        users = self.repository.list_users(skip=skip, limit=page_size)

        logger.info("Fetched %d users (page=%d, page_size=%d)", len(users), page, page_size)
        return GetListUserResponse(
            total=total,
            items=[UserItem.model_validate(u) for u in users],
        )
