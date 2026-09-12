"""Data access layer: talks to the DB only, no business rules here."""
from sqlalchemy.orm import Session

from app.common.models import User


class GetUserByIdRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)
