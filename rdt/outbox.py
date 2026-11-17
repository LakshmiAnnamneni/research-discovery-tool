"""Write drafts to disk (.eml), keep a contact log, and optionally send via SMTP.

Sending is opt-in and requires --send plus SMTP credentials in the environment.
The contact log prevents emailing the same person twice and honours a
do-not-contact list.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .composer import Draft


class ContactLog:
    def __init__(self, path: Path, do_not_contact: list[str] | None = None):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"contacted": {}, "papers": []}
        self.dnc = {e.lower() for e in (do_not_contact or [])}

    def seen_paper(self, key: str) -> bool:
        return key in self.data["papers"]

    def blocked(self, email: str) -> str | None:
        e = email.lower()
        domain = e.split("@")[-1]
        if e in self.dnc or f"@{domain}" in self.dnc:
            return "do-not-contact list"
        if e in self.data["contacted"]:
            return f"already contacted on {self.data['contacted'][e]['date']}"
        return None

    def record(self, draft: Draft, status: str) -> None:
        self.data["contacted"][draft.to_email.lower()] = {
            "date": datetime.now(timezone.utc).date().isoformat(), "paper": draft.paper_key, "status": status}
        if draft.paper_key not in self.data["papers"]:
            self.data["papers"].append(draft.paper_key)

    def mark_paper(self, key: str) -> None:
        if key not in self.data["papers"]:
            self.data["papers"].append(key)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))


def to_message(d: Draft, sender: dict) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((sender.get("name", ""), sender.get("email", "")))
    m["To"] = formataddr((d.to_name, d.to_email))
    m["Subject"] = d.subject
    m["Message-ID"] = make_msgid(domain=(sender.get("email", "rdt.local").split("@")[-1]))
    m["X-RDT-Paper"] = d.paper_key
    m["X-Unsent"] = "1"  # Outlook / Apple Mail open .eml as an editable draft
    m.set_content(d.body)
    return m


def write_eml(d: Draft, sender: dict, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{d.paper_key}_{d.to_name}")[:120]
    path = folder / f"{safe}.eml"
    path.write_bytes(bytes(to_message(d, sender)))
    return path


def send_smtp(drafts: list[Draft], sender: dict) -> list[tuple[Draft, str]]:
    host = os.environ["RDT_SMTP_HOST"]
    port = int(os.environ.get("RDT_SMTP_PORT", "587"))
    user = os.environ["RDT_SMTP_USER"]
    pwd = os.environ["RDT_SMTP_PASSWORD"]
    results = []
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pwd)
        for d in drafts:
            msg = to_message(d, sender)
            del msg["X-Unsent"]
            try:
                s.send_message(msg)
                results.append((d, "sent"))
            except smtplib.SMTPException as e:
                results.append((d, f"error: {e}"))
    return results
