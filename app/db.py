from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def session_factory(database_url: str):
    if make_url(database_url).drivername != "postgresql+psycopg":
        raise ValueError("Only postgresql+psycopg databases are supported")
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        },
    )
    return sessionmaker(engine, expire_on_commit=False)


@lru_cache
def get_factory(database_url: str):
    return session_factory(database_url)


def get_session() -> Iterator[Session]:
    with get_factory(get_settings().database_url)() as session:
        yield session
