
import pytest
from unittest.mock import Mock

from app import db


def test_get_db_closes_session(monkeypatch):
    session = Mock()
    monkeypatch.setattr(db, "SessionLocal", lambda: session)

    generator = db.get_db()

    assert next(generator) is session

    with pytest.raises(StopIteration):
        next(generator)

    session.rollback.assert_not_called()
    session.close.assert_called_once()


def test_get_db_rolls_back_and_closes_on_error(monkeypatch):
    session = Mock()
    monkeypatch.setattr(db, "SessionLocal", lambda: session)

    generator = db.get_db()

    assert next(generator) is session

    with pytest.raises(RuntimeError, match="test error"):
        generator.throw(RuntimeError("test error"))

    session.rollback.assert_called_once()
    session.close.assert_called_once()
