import uuid

import pytest

from app.domain.errors import ScopeError
from app.persistence.models import Client, Project
from app.persistence.repositories import ScopedRepository


class ProjectRepo(ScopedRepository[Project]):
    model = Project


def test_scoped_repo_requires_client_id(session):
    with pytest.raises(ScopeError):
        ProjectRepo(session, None)  # type: ignore[arg-type]


def test_repo_never_returns_other_clients_rows(factory):
    with factory() as s:
        a = Client(name=f"a-{uuid.uuid4().hex[:8]}")
        b = Client(name=f"b-{uuid.uuid4().hex[:8]}")
        s.add_all([a, b])
        s.flush()
        pa = Project(client_id=a.id, name="pa")
        pb = Project(client_id=b.id, name="pb")
        s.add_all([pa, pb])
        s.commit()
        repo_a = ProjectRepo(s, a.id)
        assert repo_a.get(pa.id) is not None
        assert repo_a.get(pb.id) is None
        assert [p.name for p in repo_a.list()] == ["pa"]


def test_cannot_add_foreign_object(session):
    with pytest.raises(ScopeError):
        ProjectRepo(session, uuid.uuid4()).add(Project(client_id=uuid.uuid4(), name="x"))
