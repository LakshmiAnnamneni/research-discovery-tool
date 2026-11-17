"""Command-line entry point.

  python -m rdt run -c config.toml            # discover, extract, draft (.eml)
  python -m rdt run -c config.toml --dry-run  # same, but don't update the contact log
  python -m rdt send runs/<stamp>             # send staged drafts via SMTP (asks to confirm)
"""
from __future__ import annotations

import argparse
import email
import email.policy
import email.utils
import sys
from pathlib import Path

from .agent import load_config, run
from .composer import Draft
from .outbox import ContactLog, send_smtp


def cmd_run(a) -> int:
    cfg = load_config(a.config)
    if a.days:
        cfg.setdefault("search", {})["days"] = a.days
    if a.keywords:
        cfg.setdefault("search", {})["keywords"] = [k.strip() for k in a.keywords.split(",") if k.strip()]
    if a.sources:
        cfg["_only_sources"] = [n.strip() for n in a.sources.split(",") if n.strip()]
    res = run(cfg, a.out, dry_run=a.dry_run)
    print(f"\nDone. {len(res.drafts)} drafts, {len(res.skipped)} skipped.")
    print(f"Review: {res.out_dir / 'report.md'}")
    print(f"Drafts: {res.out_dir / 'drafts'}  (open .eml files in your mail client to edit and send)")
    return 0


def cmd_send(a) -> int:
    cfg = load_config(a.config)
    sender = cfg.get("sender", {})
    files = sorted((Path(a.run_dir) / "drafts").glob("*.eml"))
    if not files:
        print("No drafts found.")
        return 1
    drafts = []
    for f in files:
        m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
        name, addr = email.utils.parseaddr(m["To"])
        drafts.append(Draft(m.get("X-RDT-Paper", f.stem), name, addr, m["Subject"],
                            m.get_body().get_content(), "eml", None))
    print(f"About to send {len(drafts)} emails from {sender.get('email')}:")
    for d in drafts:
        print(f"  - {d.to_name} <{d.to_email}>: {d.subject}")
    if not a.yes and input("Type 'send' to confirm: ").strip().lower() != "send":
        print("Aborted.")
        return 1
    log = ContactLog(Path(cfg.get("outreach", {}).get("log_path", "rdt_contact_log.json")))
    for d, status in send_smtp(drafts, sender):
        print(f"  {status}: {d.to_email}")
        log.record(d, status)
    log.save()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rdt", description="Research Discovery Tool")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="discover papers and draft author emails")
    r.add_argument("-c", "--config", default="config.toml")
    r.add_argument("-o", "--out", default="runs")
    r.add_argument("--days", type=int, help="override look-back window")
    r.add_argument("--keywords", help="override keywords (comma-separated)")
    r.add_argument("--sources", help="run only these configured sources (comma-separated names)")
    r.add_argument("--dry-run", action="store_true", help="don't update the contact log")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("send", help="send drafts from a run folder via SMTP")
    s.add_argument("run_dir")
    s.add_argument("-c", "--config", default="config.toml")
    s.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")
    s.set_defaults(fn=cmd_send)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
