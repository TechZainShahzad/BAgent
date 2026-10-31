"""Client-scoped repository base. Every query is filtered by client_id; unscoped access is a bug."""

import uuid
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import ScopeError


class ScopedRepository[T]:
    model: ClassVar[type[Any]]

    def __init__(self, session: Session, client_id: uuid.UUID) -> None:
        if client_id is None:
            raise ScopeError("client_id is required")
        self.session = session
        self.client_id = client_id

    def add(self, obj: T) -> T:
        if getattr(obj, "client_id", None) != self.client_id:
            raise ScopeError("object client_id does not match repository scope")
        self.session.add(obj)
        return obj

    def get(self, obj_id: uuid.UUID) -> T | None:
        stmt = select(self.model).where(
            self.model.id == obj_id, self.model.client_id == self.client_id
        )
        return self.session.scalars(stmt).first()

    def list(self, limit: int = 100) -> list[T]:
        stmt = select(self.model).where(self.model.client_id == self.client_id).limit(limit)
        return list(self.session.scalars(stmt))
