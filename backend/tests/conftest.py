import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.persistence.models import Client


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().test_database_url
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def session(factory):
    s: Session = factory()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def client_id(factory):
    """A committed client unique to the test (audit_events is append-only, so no truncation)."""
    with factory() as s:
        c = Client(name=f"client-{uuid.uuid4().hex[:10]}")
        s.add(c)
        s.commit()
        return c.id
