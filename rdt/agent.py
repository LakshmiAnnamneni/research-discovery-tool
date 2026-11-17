"""RDT agent: discover -> rank -> extract contacts -> compose -> stage drafts."""
from __future__ import annotations

import csv
import json
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import contacts, relevance
from .composer import Draft, compose_llm, compose_template
from .http import HttpClient
from .models import Paper
from .outbox import ContactLog, write_eml
from .sources import configured_sources
from .sources import fetch as fetch_source

CONF_RANK = {"low": 1, "medium": 2, "high": 3}


def load_config(path: str | Path) -> dict:
    cfg = tomllib.loads(Path(path).read_text())
    base = Path(path).parent
    cfg["_config_dir"] = str(base.resolve())  # custom source files resolve relative to the config
    configured_sources(cfg.get("sources"))     # fail fast on a bad [[sources]] block
    comp = cfg.setdefault("compose", {})
    for key in ("subject_template", "body_template"):
        f = comp.get(key + "_file")
        if f:
            comp[key] = (base / f).read_text()
    return cfg


@dataclass
class RunResult:
    out_dir: Path
    papers: list[Paper] = field(default_factory=list)
    drafts: list[Draft] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (paper_key, reason)


def discover(cfg: dict, http) -> list[Paper]:
    s = cfg.get("search", {})
    kws, days = s.get("keywords", []), int(s.get("days", 7))
    specs = configured_sources(cfg.get("sources"), cfg.get("_only_sources"))
    if not specs:
        raise ValueError("No sources configured. Add at least one [[sources]] block to your config "
                         "(see config.example.toml).")
    found: list[Paper] = []

    for spec in specs:
        print(f"Querying {spec['name']} ...")
        try:
            got = fetch_source(spec, http, kws, days, cfg.get("_config_dir"))
        except Exception as e:
            print(f"  ! {spec['name']} failed: {e}")
            continue
        print(f"  {len(got)} papers")
        found += got

    # de-duplicate on normalised title (same work can be cross-posted)
    uniq: dict[str, Paper] = {}
    for p in found:
        uniq.setdefault("".join(ch for ch in p.title.lower() if ch.isalnum()), p)
    print(f"  {len(found)} retrieved, {len(uniq)} unique")
    return list(uniq.values())


def run(cfg: dict, out_root: str | Path = "runs", http=None, dry_run: bool = False) -> RunResult:
    sender = cfg.get("sender", {})
    http = http or HttpClient(contact_email=sender.get("email", "unknown@example.com"))
    s, c, comp, o = cfg.get("search", {}), cfg.get("contacts", {}), cfg.get("compose", {}), cfg.get("outreach", {})

    out = Path(out_root) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    res = RunResult(out_dir=out)
    log = ContactLog(Path(o.get("log_path", "rdt_contact_log.json")), o.get("do_not_contact", []))

    # 1. discover + 2. rank
    papers = discover(cfg, http)
    ranked = relevance.rank(papers, s.get("keywords", []), float(s.get("min_score", 0.35)),
                            s.get("exclude", []), s.get("must_have", []))
    fresh = [p for p in ranked if not log.seen_paper(p.key)]
    res.papers = fresh[: int(s.get("max_papers", 25))]
    print(f"Relevant: {len(ranked)} (new: {len(fresh)}); processing top {len(res.papers)}")

    # 3. contacts
    min_conf = CONF_RANK[c.get("min_confidence", "medium")]
    max_emails = int(o.get("max_emails_per_run", 10))
    for p in res.papers:
        if c.get("fetch_pdfs", True):
            contacts.enrich(p, http, int(c.get("max_pages", 3)))
        author = p.contact()
        if not author:
            res.skipped.append((p.key, "no author email found"))
            continue
        if CONF_RANK.get(author.email_confidence or "low", 1) < min_conf:
            res.skipped.append((p.key, f"email confidence {author.email_confidence} below threshold"))
            continue
        why = log.blocked(author.email)
        if why:
            res.skipped.append((p.key, why))
            continue
        if len(res.drafts) >= max_emails:
            res.skipped.append((p.key, "per-run email cap reached"))
            continue

        # 4. compose
        if comp.get("mode", "template") == "llm":
            d = compose_llm(p, author, sender, comp.get("model", "claude-sonnet-5-5"))
        else:
            d = compose_template(p, author, sender, comp.get("subject_template"), comp.get("body_template"))
        res.drafts.append(d)

    # 5. stage outputs
    for d in res.drafts:
        write_eml(d, sender, out / "drafts")
        if not dry_run:
            log.record(d, "drafted")
    if not dry_run:
        for p in res.papers:
            log.mark_paper(p.key)
        log.save()
    write_reports(res)
    return res


def write_reports(res: RunResult) -> None:
    out = res.out_dir
    (out / "papers.json").write_text(json.dumps([p.to_dict() for p in res.papers], indent=2))
    with open(out / "contacts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["paper_key", "title", "relevance", "author", "email", "confidence", "corresponding", "affiliation", "url"])
        for p in res.papers:
            for a in p.authors:
                w.writerow([p.key, p.title, p.relevance, a.name, a.email or "", a.email_confidence or "",
                            "yes" if a.is_corresponding else "", a.affiliation or "", p.url])
    drafted = {d.paper_key: d for d in res.drafts}
    skipped = dict(res.skipped)
    lines = [f"# RDT run {out.name}", "",
             f"{len(res.papers)} relevant papers, {len(res.drafts)} drafts, {len(res.skipped)} skipped.", "",
             "| # | Score | Paper | Contact | Status |", "|---|---|---|---|---|"]
    for i, p in enumerate(res.papers, 1):
        d = drafted.get(p.key)
        contact = f"{d.to_name} <{d.to_email}> ({d.email_confidence})" if d else "-"
        status = "draft ready" if d else f"skipped: {skipped.get(p.key, '')}"
        lines.append(f"| {i} | {p.relevance:.2f} | [{p.title}]({p.url}) | {contact} | {status} |")
    (out / "report.md").write_text("\n".join(lines) + "\n")
