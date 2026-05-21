"""Shared pytest fixtures.

Swap the global DB engine to an in-memory sqlite for the whole test session,
so app.delivery.session_scope (which imports SessionLocal at module load) sees
the test DB without any monkeypatching at call sites.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db
from app.models import Base


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", TestSession)

    yield TestSession
    engine.dispose()
