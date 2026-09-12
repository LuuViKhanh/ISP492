"""Data access layer: talks to the DB only, no business rules here."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.models import User


class GetListUserRepository:
    def __init__(self, db: Session):
        self.db = db

    def count_all(self) -> int:
        return self.db.scalar(select(func.count()).select_from(User)) or 0

    def list_users(self, skip: int, limit: int) -> list[User]:
        stmt = select(User).order_by(User.id).offset(skip).limit(limit)
        return list(self.db.scalars(stmt).all())
