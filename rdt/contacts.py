"""Author contact extraction.

Pre-print metadata APIs rarely expose emails, so we read the first pages of the
PDF, pull out email addresses (including grouped forms like {alice,bob}@uni.edu),
and attribute each one to an author by comparing the address's local part with
author names. Every attribution carries a confidence level.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import unicodedata

from .models import Author, Paper

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
GROUPED_RE = re.compile(r"\{([^}]{3,300})\}\s*@\s*([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})")
CORR_RE = re.compile(r"(correspond\w*|contact)\s*(author)?[^\n@]{0,80}?(" + EMAIL_RE.pattern + ")", re.IGNORECASE)
IGNORE_LOCAL = {"noreply", "no-reply", "info", "support", "admin", "permissions", "editor", "help", "webmaster"}
IGNORE_DOMAINS = {"example.com", "arxiv.org", "biorxiv.org", "medrxiv.org"}


# ---------- PDF text ----------

def pdf_to_text(pdf_bytes: bytes, max_pages: int = 3) -> str:
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(pdf_bytes)
            f.flush()
            r = subprocess.run(["pdftotext", "-l", str(max_pages), "-layout", f.name, "-"],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", "replace")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:max_pages])
    except Exception:
        return ""


# ---------- email extraction ----------

def _clean(addr: str) -> str:
    return addr.strip().strip(".,;:()[]<>").lower()


def extract_emails(text: str) -> tuple[list[str], set[str]]:
    """Return (emails in order of appearance, emails flagged as corresponding)."""
    text = text.replace(" ", " ")
    text = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)\s+@\s*(?=\w)|(?<=\w)\s*@\s+(?=\w)", "@", text)
    found: list[str] = []

    for m in GROUPED_RE.finditer(text):
        for local in re.split(r"[,;|\s]+", m.group(1)):
            local = local.strip()
            if local:
                found.append(f"{local}@{m.group(2)}".lower())
    found.extend(_clean(e) for e in EMAIL_RE.findall(text))

    seen, ordered = set(), []
    for e in found:
        local, _, domain = e.partition("@")
        if e in seen or local in IGNORE_LOCAL or domain in IGNORE_DOMAINS:
            continue
        seen.add(e)
        ordered.append(e)
    corresponding = {_clean(m.group(3)) for m in CORR_RE.finditer(text)}
    return ordered, corresponding & seen


# ---------- attribution ----------

def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _name_tokens(name: str) -> tuple[str, str, list[str]]:
    toks = [t for t in re.split(r"[\s\-.]+", _ascii(name)) if t]
    if not toks:
        return "", "", []
    return toks[0], toks[-1], toks


def match_score(email: str, name: str) -> int:
    """0 = no match, 1 = weak (initials), 2 = surname present, 3 = strong (first+last / first.last)."""
    local = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    first, last, toks = _name_tokens(name)
    if not last or not local:
        return 0
    if len(last) >= 3 and last in local:
        if first and (first in local or local.startswith(first[0]) or local.endswith(first[0])):
            return 3
        return 2
    if len(first) >= 3 and first in local:
        return 2
    initials = "".join(t[0] for t in toks)
    if len(initials) >= 2 and (local == initials or local == initials[::-1] or local.startswith(initials)):
        return 1
    return 0


def attribute(authors: list[Author], emails: list[str], corresponding: set[str]) -> list[Author]:
    """Greedy one-to-one assignment of emails to authors by match strength."""
    pairs = []
    for e in emails:
        for i, a in enumerate(authors):
            s = match_score(e, a.name)
            if s:
                pairs.append((s, i, e))
    pairs.sort(key=lambda x: -x[0])
    used_e, used_a = set(), set()
    for s, i, e in pairs:
        if e in used_e or i in used_a:
            continue
        a = authors[i]
        a.email = e
        a.email_confidence = {3: "high", 2: "medium", 1: "low"}[s]
        if e in corresponding:
            a.is_corresponding = True
        used_e.add(e)
        used_a.add(i)

    # A single unmatched corresponding email: attach to the flagged corresponding author if there is one.
    leftover = [e for e in corresponding if e not in used_e]
    if len(leftover) == 1:
        for a in authors:
            if a.is_corresponding and not a.email:
                a.email, a.email_confidence = leftover[0], "medium"
                break
    return authors


def enrich(paper: Paper, http, max_pages: int = 3) -> Paper:
    """Download the PDF and fill in author emails. Network/parse failures leave the paper unchanged."""
    if not paper.pdf_url:
        return paper
    try:
        text = pdf_to_text(http.get(paper.pdf_url), max_pages=max_pages)
    except Exception as e:  # pragma: no cover - network dependent
        print(f"  ! could not fetch PDF for {paper.key}: {e}")
        return paper
    emails, corr = extract_emails(text)
    attribute(paper.authors, emails, corr)
    return paper
