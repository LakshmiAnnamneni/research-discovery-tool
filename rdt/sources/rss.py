"""Generic feed source: RSS 2.0, RSS 1.0/RDF (common for journals) and Atom.

Elements are matched by local name, so the namespace a publisher uses (dc:, prism:,
atom:, rss1:) doesn't matter. Feed items rarely carry author emails; when an item
links a PDF (or `pdf_url_template` builds one) the contacts stage reads it as usual.

Options (in the [[sources]] block):
  url                 feed URL (required)
  categories          keep only items tagged with one of these (case-insensitive)
  pdf_url_template    build a PDF link when the feed has none, e.g. "{url}.full.pdf"
                      placeholders: {url} {id}
  include_undated     keep items with no parseable date (default true)
  max_items           cap per feed (default 500)
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from ..models import Author, Paper

DATE_FIELDS = ("published", "pubDate", "date", "publicationDate", "coverDate", "issued", "updated")
TEXT_FIELDS = ("description", "summary", "abstract", "encoded", "content")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el, *names: str) -> str:
    for name in names:
        for c in el:
            if _local(c.tag) == name:
                t = "".join(c.itertext()).strip()
                if t:
                    return t
    return ""


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)            # HTML in descriptions
    return " ".join(html.unescape(s).split())


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).date()  # RFC 822: "Mon, 16 Nov 2026 09:00:00 GMT"
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        pass
    m = re.match(r"\d{4}-\d{2}-\d{2}", raw)
    return date.fromisoformat(m.group()) if m else None


def _split_names(raw: str) -> list[str]:
    raw = " ".join(raw.split())
    if ";" in raw:                                            # "Smith, J.; Doe, A."
        parts = [p.strip() for p in raw.split(";")]
        return [" ".join(reversed([x.strip() for x in p.split(",", 1)])) if "," in p else p
                for p in parts if p]
    if raw.count(",") == 1 and len(raw.split(",")[0].split()) == 1:  # single "Smith, J. A." / "Doe, Alice B."
        last, first = raw.split(",", 1)
        return [f"{first.strip()} {last.strip()}"]
    return [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+|\s*&\s*", raw) if p.strip()]


def _authors(item) -> list[Author]:
    names: list[str] = []
    for c in item:
        tag = _local(c.tag)
        if tag == "creator":
            names += _split_names("".join(c.itertext()))
        elif tag in ("author", "contributor"):
            n = _text(c, "name")
            if not n:
                raw = (c.text or "").strip()
                m = re.search(r"\(([^)]+)\)", raw)                 # RSS 2.0: "x@y.edu (Jane Doe)"
                n = m.group(1) if m else ("" if "@" in raw else raw)
            if n:
                names.append(" ".join(n.split()))
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(Author(name=n))
    return out


def _links(item) -> tuple[str | None, str | None]:
    link = pdf = None
    for c in item:
        tag = _local(c.tag)
        if tag == "link":
            href = c.get("href") or (c.text or "").strip()
            if not href:
                continue
            if c.get("type") == "application/pdf" or href.lower().endswith(".pdf"):
                pdf = pdf or href
            elif c.get("rel", "alternate") == "alternate":
                link = link or href
        elif tag == "enclosure":
            u = c.get("url") or ""
            if c.get("type") == "application/pdf" or u.lower().endswith(".pdf"):
                pdf = pdf or u
    return link, pdf


def parse_feed(data: bytes, name: str, pdf_url_template: str | None = None) -> list[Paper]:
    root = ET.fromstring(data)
    papers = []
    for item in (e for e in root.iter() if _local(e.tag) in ("item", "entry")):
        title = _clean(_text(item, "title"))
        if not title:
            continue
        link, pdf = _links(item)
        about = item.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about")  # RSS 1.0
        link = link or about or ""
        ident = _text(item, "doi", "identifier", "guid", "id") or link or title
        published = ""
        for f in DATE_FIELDS:
            d = _parse_date(_text(item, f)) if _text(item, f) else None
            if d:
                published = d.isoformat()
                break
        if not pdf and pdf_url_template and link:
            pdf = pdf_url_template.format(url=link, id=ident)
        cats = [(c.get("term") or "".join(c.itertext())).strip().lower()
                for c in item if _local(c.tag) in ("category", "subject")]
        papers.append(Paper(
            source=name,
            id=ident,
            title=title,
            abstract=_clean(_text(item, *TEXT_FIELDS)),
            authors=_authors(item),
            published=published,
            url=link,
            pdf_url=pdf,
            categories=[c for c in cats if c],
        ))
    return papers


def search(http, spec: dict, keywords: list[str], days: int) -> list[Paper]:
    papers = parse_feed(http.get(spec["url"]), spec["name"], spec.get("pdf_url_template"))
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    keep_undated = spec.get("include_undated", True)
    papers = [p for p in papers if (p.published >= cutoff if p.published else keep_undated)]
    wanted = {c.lower() for c in spec.get("categories", [])}
    if wanted:
        papers = [p for p in papers if set(p.categories) & wanted]
    return papers[: int(spec.get("max_items", 500))]
