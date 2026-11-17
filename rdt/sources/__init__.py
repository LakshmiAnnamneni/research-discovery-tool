"""Source registry.

Only the sources listed in the config run; there are no implicit defaults.

Config format (preferred) - one [[sources]] block per source:

    [[sources]]
    type = "arxiv"                 # arxiv | biorxiv | medrxiv | rss | python
    categories = ["cs.LG"]

    [[sources]]
    name  = "ssrn-finance"         # required for rss/python; used in paper keys and logs
    type  = "rss"
    url   = "https://.../feed.xml"
    label = "SSRN"                 # optional: how the source is named in emails

    [[sources]]
    name = "my-source"
    type = "python"
    function = "my_sources.py:search"   # file relative to the config, or "package.module:function"

The legacy table form ([sources.arxiv], [sources.biorxiv] ...) is still accepted;
a table that is present runs unless it sets enabled = false.

Every source function has the signature
    fn(http, spec: dict, keywords: list[str], days: int) -> list[Paper | dict]
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Callable

from ..models import Author, Paper
from . import arxiv, biorxiv, rss

BUILTIN_TYPES = ("arxiv", "biorxiv", "medrxiv")


def _arxiv(http, spec: dict, keywords: list[str], days: int) -> list[Paper]:
    return arxiv.search(http, keywords, spec.get("categories", []), days, int(spec.get("max_results", 100)))


def _rxiv(server: str) -> Callable:
    def fn(http, spec: dict, keywords: list[str], days: int) -> list[Paper]:
        return biorxiv.search(http, server, spec.get("categories", []), days, int(spec.get("max_records", 2000)))
    return fn


HANDLERS: dict[str, Callable] = {
    "arxiv": _arxiv,
    "biorxiv": _rxiv("biorxiv"),
    "medrxiv": _rxiv("medrxiv"),
    "rss": rss.search,
}
TYPES = (*HANDLERS, "python")


def configured_sources(sources: Any, only: list[str] | None = None) -> list[dict]:
    """Normalise and validate the config's sources. Raises ValueError on a bad entry."""
    if not sources:
        items: list[dict] = []
    elif isinstance(sources, dict):  # legacy [sources.<name>] tables
        items = [{"name": k, "type": v.get("type", k), **v} for k, v in sources.items()]
    elif isinstance(sources, list):
        items = [dict(s) for s in sources]
    else:
        raise ValueError("'sources' must be a list of [[sources]] blocks")

    out: list[dict] = []
    seen: set[str] = set()
    for i, s in enumerate(items, 1):
        if not s.get("enabled", True):
            continue
        typ = s.get("type")
        where = f"source #{i}" + (f" ({s['name']})" if s.get("name") else "")
        if typ not in TYPES:
            raise ValueError(f"{where}: unknown type {typ!r}; use one of {', '.join(TYPES)}")
        if not s.get("name"):
            if typ not in BUILTIN_TYPES:
                raise ValueError(f"{where}: a {typ} source needs a 'name'")
            s["name"] = typ
        if typ == "rss" and not s.get("url"):
            raise ValueError(f"{where}: an rss source needs a 'url'")
        if typ == "python" and not s.get("function"):
            raise ValueError(f"{where}: a python source needs 'function', e.g. \"my_sources.py:search\"")
        if s["name"] in seen:
            raise ValueError(f"{where}: duplicate source name {s['name']!r}")
        seen.add(s["name"])
        out.append(s)

    if only:
        unknown = set(only) - seen
        if unknown:
            raise ValueError(f"--sources: no enabled source named {', '.join(sorted(unknown))} "
                             f"(configured: {', '.join(sorted(seen)) or 'none'})")
        out = [s for s in out if s["name"] in only]
    return out


def fetch(spec: dict, http, keywords: list[str], days: int, base_dir: str | Path | None = None) -> list[Paper]:
    """Run one configured source and return its papers."""
    typ = spec["type"]
    if typ == "python":
        fn = load_function(spec["function"], base_dir)
        papers = [to_paper(r, spec["name"]) for r in (fn(http, spec, keywords, days) or [])]
    else:
        papers = HANDLERS[typ](http, spec, keywords, days)
    if typ not in BUILTIN_TYPES:
        for p in papers:  # namespace keys by source name so the contact log stays unambiguous
            p.source = spec["name"]
    if spec.get("label"):
        for p in papers:
            p.source_name = spec["label"]
    return papers


def load_function(ref: str, base_dir: str | Path | None = None) -> Callable:
    """'path/to/file.py:func' (relative to the config file) or 'package.module:func'."""
    target, _, attr = ref.rpartition(":")
    if not target or not attr:
        raise ValueError(f"function must look like 'my_sources.py:search', got {ref!r}")
    if target.endswith(".py"):
        path = Path(target)
        if not path.is_absolute():
            path = Path(base_dir or ".") / path
        if not path.is_file():
            raise ValueError(f"source file not found: {path}")
        spec = importlib.util.spec_from_file_location(f"rdt_user_source_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise ValueError(f"{target} has no function {attr!r}")
    return fn


def to_paper(item: Paper | dict, source: str) -> Paper:
    """Accept a Paper, or a plain dict with at least 'title' (authors may be names or dicts)."""
    if isinstance(item, Paper):
        return item
    if not isinstance(item, dict) or not item.get("title"):
        raise TypeError(f"custom source returned {type(item).__name__}; expected Paper or dict with 'title'")
    authors = []
    for a in item.get("authors", []):
        if isinstance(a, Author):
            authors.append(a)
        elif isinstance(a, dict):
            authors.append(Author(**a))
        else:
            authors.append(Author(name=str(a)))
    return Paper(
        source=source,
        id=str(item.get("id") or item.get("url") or item["title"]),
        title=" ".join(str(item["title"]).split()),
        abstract=" ".join(str(item.get("abstract", "")).split()),
        authors=authors,
        published=str(item.get("published", ""))[:10],
        url=item.get("url", ""),
        pdf_url=item.get("pdf_url"),
        categories=list(item.get("categories", [])),
    )
