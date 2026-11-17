"""Compose outreach emails to authors.

Two modes:
  * template (default) - deterministic, uses string.Template placeholders.
  * llm               - asks Claude (Anthropic Messages API) for a short, specific
                        note grounded in the abstract; falls back to the template
                        on any error.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from string import Template

from .models import Author, Paper

DEFAULT_SUBJECT = "Your recent pre-print: \"$title_short\""
DEFAULT_BODY = """Dear $salutation,

I came across your recent pre-print, "$title", posted on $source_name on $published, and wanted to reach out.

$purpose

The work stood out because of its focus on $matched_terms. I would welcome the chance to $ask.

Thank you for your time, and congratulations on the paper.

Best regards,
$sender_name
$sender_title
$sender_org
$sender_email

---
You are receiving this one-time note because you are listed as an author of $url.
If you would prefer not to hear from me again, simply reply and let me know.
"""

SOURCE_NAMES = {"arxiv": "arXiv", "biorxiv": "bioRxiv", "medrxiv": "medRxiv"}


@dataclass
class Draft:
    paper_key: str
    to_name: str
    to_email: str
    subject: str
    body: str
    mode: str
    email_confidence: str | None


def _surname(name: str) -> str:
    return name.split()[-1] if name.split() else name


def _fields(paper: Paper, author: Author, sender: dict) -> dict:
    title_short = paper.title if len(paper.title) <= 70 else paper.title[:67].rsplit(" ", 1)[0] + "..."
    terms = paper.matched_terms or ["this area"]
    terms_txt = terms[0] if len(terms) == 1 else ", ".join(terms[:-1]) + " and " + terms[-1]
    return {
        "salutation": sender.get("salutation_style", "Dr.") + " " + _surname(author.name),
        "author_name": author.name,
        "title": paper.title,
        "title_short": title_short,
        "source_name": paper.source_name or SOURCE_NAMES.get(paper.source, paper.source),
        "published": paper.published,
        "url": paper.url,
        "matched_terms": terms_txt,
        "purpose": sender.get("purpose", ""),
        "ask": sender.get("ask", "discuss the work briefly"),
        "sender_name": sender.get("name", ""),
        "sender_title": sender.get("title", ""),
        "sender_org": sender.get("organization", ""),
        "sender_email": sender.get("email", ""),
    }


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def compose_template(paper: Paper, author: Author, sender: dict,
                     subject_tpl: str | None = None, body_tpl: str | None = None) -> Draft:
    f = _fields(paper, author, sender)
    return Draft(
        paper_key=paper.key, to_name=author.name, to_email=author.email or "",
        subject=Template(subject_tpl or DEFAULT_SUBJECT).safe_substitute(f),
        body=_tidy(Template(body_tpl or DEFAULT_BODY).safe_substitute(f)),
        mode="template", email_confidence=author.email_confidence,
    )


LLM_SYSTEM = (
    "You write brief, professional first-contact emails from a practitioner to the author of a research pre-print. "
    "Rules: 120-180 words in the body; reference one specific idea from the abstract accurately; no flattery beyond one "
    "sentence; never invent results, numbers or affiliations not in the input; one clear ask; plain text, no markdown. "
    "Return JSON only: {\"subject\": \"...\", \"body\": \"...\"}. The body must start with the salutation and end with "
    "the sender signature block exactly as given."
)


def compose_llm(paper: Paper, author: Author, sender: dict, model: str, api_key: str | None = None,
                timeout: int = 60) -> Draft:
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    fallback = compose_template(paper, author, sender)
    if not api_key:
        return fallback
    f = _fields(paper, author, sender)
    signature = "\n".join(x for x in [f["sender_name"], f["sender_title"], f["sender_org"], f["sender_email"]] if x)
    user = json.dumps({
        "salutation": f"Dear {f['salutation']},",
        "paper": {"title": paper.title, "abstract": paper.abstract[:3000], "server": f["source_name"],
                  "posted": paper.published, "url": paper.url},
        "recipient": author.name,
        "why_relevant": paper.matched_terms,
        "sender_purpose": f["purpose"],
        "ask": f["ask"],
        "signature": signature,
    })
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": 800, "system": LLM_SYSTEM,
                         "messages": [{"role": "user", "content": user}]}).encode(),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        subject, body = data.get("subject", "").strip(), data.get("body", "").strip()
        if not subject or not body:
            raise ValueError("empty LLM output")
        footer = DEFAULT_BODY.split("---", 1)[1]
        body += "\n\n---" + Template(footer).safe_substitute(f)
        return Draft(paper.key, author.name, author.email or "", subject, _tidy(body), "llm", author.email_confidence)
    except Exception as e:
        print(f"  ! LLM compose failed for {paper.key} ({e}); using template")
        return fallback
