"""Core data models for the Research Discovery Tool."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Author:
    name: str
    email: Optional[str] = None
    affiliation: Optional[str] = None
    is_corresponding: bool = False
    # How confident we are the email belongs to this author: high | medium | low
    email_confidence: Optional[str] = None


@dataclass
class Paper:
    source: str               # arxiv | biorxiv | medrxiv
    id: str                   # arXiv id or DOI
    title: str
    abstract: str
    authors: list[Author]
    published: str            # ISO date
    url: str
    pdf_url: Optional[str] = None
    categories: list[str] = field(default_factory=list)
    relevance: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    def contact(self) -> Optional[Author]:
        """Best author to contact: corresponding author with email, else first author with email."""
        with_email = [a for a in self.authors if a.email]
        for a in with_email:
            if a.is_corresponding:
                return a
        return with_email[0] if with_email else None

    def to_dict(self) -> dict:
        return asdict(self)
