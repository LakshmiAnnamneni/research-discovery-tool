"""arXiv pre-print server client (Atom API: https://info.arxiv.org/help/api/)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from ..models import Author, Paper

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def build_query(keywords: list[str], categories: list[str]) -> str:
    """keywords -> OR'd over title+abstract; categories (e.g. cs.LG, q-bio.GN) -> OR'd; groups AND'd."""
    parts = []
    if keywords:
        kw = []
        for k in keywords:
            term = f'"{k}"' if " " in k else k
            kw.append(f"ti:{term} OR abs:{term}")
        parts.append("(" + " OR ".join(kw) + ")")
    if categories:
        parts.append("(" + " OR ".join(f"cat:{c}" for c in categories) + ")")
    return " AND ".join(parts) if parts else "all:*"


def parse_feed(xml_bytes: bytes) -> list[Paper]:
    root = ET.fromstring(xml_bytes)
    papers = []
    for e in root.findall("a:entry", NS):
        raw_id = (e.findtext("a:id", "", NS) or "").strip()
        arxiv_id = raw_id.rsplit("/abs/", 1)[-1]
        pdf_url = None
        for link in e.findall("a:link", NS):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href")
        authors = []
        for a in e.findall("a:author", NS):
            authors.append(Author(
                name=" ".join((a.findtext("a:name", "", NS) or "").split()),
                affiliation=(a.findtext("arxiv:affiliation", None, NS) or None),
            ))
        papers.append(Paper(
            source="arxiv",
            id=arxiv_id,
            title=" ".join((e.findtext("a:title", "", NS) or "").split()),
            abstract=" ".join((e.findtext("a:summary", "", NS) or "").split()),
            authors=authors,
            published=(e.findtext("a:published", "", NS) or "")[:10],
            url=raw_id.replace("http://", "https://"),
            pdf_url=(pdf_url or "").replace("http://", "https://") or None,
            categories=[c.get("term") for c in e.findall("a:category", NS) if c.get("term")],
        ))
    return papers


def search(http, keywords: list[str], categories: list[str], days: int, max_results: int = 100) -> list[Paper]:
    params = {
        "search_query": build_query(keywords, categories),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    papers = parse_feed(http.get(f"{API}?{urlencode(params)}"))
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [p for p in papers if p.published >= cutoff]
