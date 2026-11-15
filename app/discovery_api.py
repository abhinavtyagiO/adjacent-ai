"""Read-only discovery views shared by brand/profile and paginated discovery endpoints."""

from sqlalchemy import func, select

from app.models import CatalogProduct, CustomerConcern, ExtractionJob, Offering


def summary(session, brand_id):
    job = session.scalar(
        select(ExtractionJob)
        .where(
            ExtractionJob.brand_id == brand_id,
            ExtractionJob.diagnostics["discovery"].is_not(None),
        )
        .order_by(ExtractionJob.created_at.desc())
        .limit(1)
    )
    total = session.scalar(
        select(func.count()).select_from(CatalogProduct).where(CatalogProduct.brand_id == brand_id)
    )
    linked = session.scalar(
        select(func.count())
        .select_from(CatalogProduct)
        .where(CatalogProduct.brand_id == brand_id, CatalogProduct.offering_id.is_not(None))
    )
    concerns = list(
        session.scalars(
            select(CustomerConcern)
            .where(CustomerConcern.brand_id == brand_id)
            .order_by(CustomerConcern.concern_key)
        )
    )
    return {
        "catalog": {
            "available": job is not None,
            "total_discovered_products": total,
            "linked_to_offerings": linked,
            "coverage": job.diagnostics.get("discovery", {}) if job else {},
            "price_semantics": "OBSERVED_AT_SOURCE_FETCH_TIME",
        },
        "customer_concerns": [{"id": str(c.id), **c.details} for c in concerns],
    }


def catalog(session, brand_id, limit, offset, category=None, linked_only=False):
    query = select(CatalogProduct).where(CatalogProduct.brand_id == brand_id)
    if category:
        query = query.where(CatalogProduct.details["categories"].contains([category]))
    if linked_only:
        query = query.where(CatalogProduct.offering_id.is_not(None))
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    offering_keys = {
        o.id: o.details.get("key")
        for o in session.scalars(select(Offering).where(Offering.brand_id == brand_id))
    }
    categories = sorted(
        {
            c
            for details in session.scalars(
                select(CatalogProduct.details).where(CatalogProduct.brand_id == brand_id)
            )
            for c in details.get("categories", [])
        }
    )
    rows = session.scalars(query.order_by(CatalogProduct.product_key).limit(limit).offset(offset))
    return {
        "items": [
            {
                "id": r.id,
                "linked_offering_id": r.offering_id,
                "linked_offering_key": offering_keys.get(r.offering_id),
                "source_job_id": r.source_job_id,
                **r.details,
            }
            for r in rows
        ],
        "total": count,
        "available_categories": categories,
        "limit": limit,
        "offset": offset,
        **summary(session, brand_id)["catalog"],
    }
