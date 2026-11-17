"""Topical relevance scoring.

Score = weighted keyword/phrase hits (title counts 3x abstract), normalised to 0..1,
with hard exclusion terms. Deterministic and explainable: every paper records
which terms matched.
"""
from __future__ import annotations

import math
import re

from .models import Paper

TITLE_WEIGHT = 3.0
ABSTRACT_WEIGHT = 1.0


def _pattern(term: str) -> re.Pattern:
    # word-boundary match; allow simple plural / hyphen variants
    esc = re.escape(term.lower()).replace(r"\ ", r"[\s\-]+")
    return re.compile(rf"\b{esc}(?:s|es)?\b", re.IGNORECASE)


def score(paper: Paper, keywords: list[str], exclude: list[str] | None = None,
          must_have: list[str] | None = None) -> Paper:
    text_t, text_a = paper.title, paper.abstract
    for x in exclude or []:
        if _pattern(x).search(text_t) or _pattern(x).search(text_a):
            paper.relevance, paper.matched_terms = 0.0, []
            return paper
    for m in must_have or []:
        if not (_pattern(m).search(text_t) or _pattern(m).search(text_a)):
            paper.relevance, paper.matched_terms = 0.0, []
            return paper

    raw, matched = 0.0, []
    for kw in keywords:
        p = _pattern(kw)
        t_hits, a_hits = len(p.findall(text_t)), len(p.findall(text_a))
        if t_hits or a_hits:
            matched.append(kw)
            # diminishing returns on repeated mentions
            raw += TITLE_WEIGHT * math.log1p(t_hits) + ABSTRACT_WEIGHT * math.log1p(a_hits)
    coverage = len(matched) / len(keywords) if keywords else 0.0
    intensity = 1 - math.exp(-raw / 3)
    paper.relevance = round(0.5 * coverage + 0.5 * intensity, 3)
    paper.matched_terms = matched
    return paper


def rank(papers: list[Paper], keywords: list[str], min_score: float,
         exclude: list[str] | None = None, must_have: list[str] | None = None) -> list[Paper]:
    scored = [score(p, keywords, exclude, must_have) for p in papers]
    return sorted((p for p in scored if p.relevance >= min_score), key=lambda p: p.relevance, reverse=True)
