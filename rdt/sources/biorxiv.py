"""bioRxiv / medRxiv client (https://api.biorxiv.org/).

The API has no keyword search: it returns everything posted in a date window,
100 records per page. We page through the window, filter by subject category
here, and leave keyword relevance to the scoring stage.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..models import Author, Paper

API = "https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}/json"
SITES = {"biorxiv": "https://www.biorxiv.org", "medrxiv": "https://www.medrxiv.org"}


def _split_authors(raw: str) -> list[str]:
    # API format: "Smith, J.; Doe, A. B.; ..."  -> "J. Smith", "A. B. Doe"
    names = []
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "," in chunk:
            last, first = [s.strip() for s in chunk.split(",", 1)]
            names.append(f"{first} {last}".strip())
        else:
            names.append(chunk)
    return names


def parse_page(server: str, payload: dict) -> list[Paper]:
    site = SITES[server]
    papers: dict[str, Paper] = {}
    for r in payload.get("collection", []):
        doi = r.get("doi", "")
        version = str(r.get("version", "1"))
        corr = (r.get("author_corresponding") or "").strip()
        corr_inst = (r.get("author_corresponding_institution") or "").strip() or None
        authors = []
        for n in _split_authors(r.get("authors", "")):
            is_corr = bool(corr) and _same_person(n, corr)
            authors.append(Author(name=n, is_corresponding=is_corr, affiliation=corr_inst if is_corr else None))
        if corr and not any(a.is_corresponding for a in authors):
            authors.append(Author(name=corr, is_corresponding=True, affiliation=corr_inst))
        p = Paper(
            source=server,
            id=doi,
            title=" ".join(r.get("title", "").split()),
            abstract=" ".join(r.get("abstract", "").split()),
            authors=authors,
            published=r.get("date", ""),
            url=f"{site}/content/{doi}v{version}",
            pdf_url=f"{site}/content/{doi}v{version}.full.pdf",
            categories=[r.get("category", "").strip().lower()] if r.get("category") else [],
        )
        papers[doi] = p  # later versions overwrite earlier ones
    return list(papers.values())


def _same_person(a: str, b: str) -> bool:
    """Same surname and (when both have one) same first initial. Both names in 'First Last' order."""
    ta = [t.strip(".").lower() for t in a.split() if t.strip(".")]
    tb = [t.strip(".").lower() for t in b.split() if t.strip(".")]
    if not ta or not tb or ta[-1] != tb[-1]:
        return False
    if len(ta) > 1 and len(tb) > 1:
        return ta[0][0] == tb[0][0]
    return True


def search(http, server: str, categories: list[str], days: int, max_records: int = 2000) -> list[Paper]:
    end = date.today()
    start = end - timedelta(days=days)
    wanted = {c.lower() for c in categories}
    out: list[Paper] = []
    cursor = 0
    while cursor < max_records:
        url = API.format(server=server, start=start.isoformat(), end=end.isoformat(), cursor=cursor)
        payload = json.loads(http.get(url))
        page = parse_page(server, payload)
        out.extend(p for p in page if not wanted or (set(p.categories) & wanted))
        msg = (payload.get("messages") or [{}])[0]
        total = int(msg.get("total", 0) or 0)
        cursor += int(msg.get("count", len(payload.get("collection", []))) or 0)
        if not payload.get("collection") or cursor >= total:
            break
    return out
