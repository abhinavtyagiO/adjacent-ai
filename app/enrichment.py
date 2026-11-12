"""Source-neutral boundary for future enrichment adapters."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Document:
    url: str
    text: str
    fetched_at: datetime
    title: str | None = None
    source_type: str = "website"


@dataclass
class SourceBundle:
    documents: list[Document]
    warnings: list[str] = field(default_factory=list)


class EnrichmentSource(Protocol):
    async def collect(self, website_url: str) -> SourceBundle:
        """Collect bounded documents with URLs and server-controlled timestamps."""
        ...
