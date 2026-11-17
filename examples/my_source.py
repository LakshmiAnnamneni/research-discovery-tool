"""Example custom source for RDT.

Wire it up in config.toml:

    [[sources]]
    name = "my-source"
    type = "python"
    function = "examples/my_source.py:search"
    api_url = "https://api.example.org/papers"   # any extra keys are passed to you in `spec`

RDT calls search() once per run. Return a list of dicts (or rdt.models.Paper objects).
Only "title" is required; the more you fill in, the better ranking and contact
extraction work. "pdf_url" lets RDT read the PDF for author emails.
"""
import json
from datetime import date, timedelta
from urllib.parse import urlencode


def search(http, spec, keywords, days):
    """
    http      polite HTTP client: http.get(url) -> bytes (rate-limited, retries, identifies you)
    spec      this source's [[sources]] block from config.toml, as a dict
    keywords  the [search] keywords
    days      look-back window in days
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    url = spec["api_url"] + "?" + urlencode({"q": " OR ".join(keywords), "since": since})
    records = json.loads(http.get(url))

    return [
        {
            "id": r["doi"],                      # stable id; used to avoid repeat outreach
            "title": r["title"],
            "abstract": r.get("abstract", ""),
            "authors": r.get("authors", []),     # ["Jane Doe", ...] or [{"name": ..., "email": ...}]
            "published": r.get("date", ""),      # "YYYY-MM-DD"
            "url": r.get("url", ""),
            "pdf_url": r.get("pdf_url"),
            "categories": r.get("subjects", []),
        }
        for r in records
    ]
