"""Phase 2 schema rollback is tested only on the disposable PostgreSQL database."""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.config import get_settings
from tests.test_creative_workflow import checksum, seed

pytestmark = pytest.mark.integration


def test_phase2_migration_rolls_back_without_mutating_phase1_profiles(
    factory, database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    brand, _ = seed(factory)
    before = checksum(factory, brand)
    try:
        command.downgrade(config, "0004")
        with factory() as s:
            assert s.scalar(text("SELECT to_regclass('creative_jobs')")) is None
        assert checksum(factory, brand) == before
    finally:
        command.upgrade(config, "head")
    with factory() as s:
        assert s.scalar(text("SELECT to_regclass('creative_jobs')")) == "creative_jobs"
    assert checksum(factory, brand) == before
    command.check(config)
    get_settings.cache_clear()
