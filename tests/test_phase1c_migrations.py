"""Upgrade/downgrade checks use only the isolated disposable PostgreSQL test DB."""

import json
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.config import get_settings

pytestmark = pytest.mark.integration


def test_0004_rollback_preserves_old_profile_and_restores_schema(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(database_url)
    brand, job = uuid.uuid4(), uuid.uuid4()
    audit = {"facts": {"available": True, "accepted": 1, "rejected": 0}}
    sections = {
        "brand_identity": {
            "website_url": f"https://migration-{brand}.example/",
            "name": "Migration sentinel",
        }
    }
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO brands(id,website_url,name,revision,created_at) "
                "VALUES (:id,:url,:name,1,now())"
            ),
            {
                "id": brand,
                "url": sections["brand_identity"]["website_url"],
                "name": "Migration sentinel",
            },
        )
        c.execute(
            text(
                "INSERT INTO profiles(brand_id,status,schema_version,sections,provenance,"
                "warnings,pipeline_metadata,updated_at) VALUES (:id,'DRAFT',2,"
                "CAST(:sections AS jsonb),'[]','[]',CAST(:meta AS jsonb),now())"
            ),
            {
                "id": brand,
                "sections": json.dumps(sections),
                "meta": json.dumps({"evidence_validation": audit}),
            },
        )
        c.execute(
            text(
                "INSERT INTO extraction_jobs(id,brand_id,status,kind,base_revision,attempts,"
                "available_at,created_at) VALUES (:job,:brand,'INSUFFICIENT_DATA',"
                "'MARKETING',1,1,now(),now())"
            ),
            {"job": job, "brand": brand},
        )
    try:
        command.downgrade(config, "0003")
        with engine.connect() as c:
            assert c.scalar(text("SELECT to_regclass('catalog_products')")) is None
            row = c.execute(
                text("SELECT sections,pipeline_metadata FROM profiles WHERE brand_id=:id"),
                {"id": brand},
            ).one()
            assert (
                row.sections == sections and row.pipeline_metadata["evidence_validation"] == audit
            )
            assert (
                c.scalar(text("SELECT status FROM extraction_jobs WHERE id=:job"), {"job": job})
                == "FAILED"
            )
    finally:
        command.upgrade(config, "head")
    with engine.connect() as c:
        assert c.scalar(text("SELECT to_regclass('catalog_products')")) == "catalog_products"
        assert (
            c.scalar(text("SELECT kind FROM extraction_jobs WHERE id=:job"), {"job": job})
            == "EXTRACTION"
        )
        assert (
            c.scalar(text("SELECT source_job_id FROM profiles WHERE brand_id=:id"), {"id": brand})
            is None
        )
    command.check(config)
    engine.dispose()
    get_settings.cache_clear()
