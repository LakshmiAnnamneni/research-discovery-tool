# Research Discovery Tool (RDT)

An agent that finds new pre-prints on a topic, works out who wrote them and how to reach them, and drafts a personalised email to each author for you to review.

```
 discover ──► rank ──► extract contacts ──► compose ──► stage drafts ──► (optional) send
 arXiv         keyword    PDF first pages     template     .eml files       SMTP, after
 bioRxiv       scoring,   → emails → matched   or Claude    report.md        you confirm
 medRxiv       exclusions   to authors                      contacts.csv
```

## Quick start

```bash
# Python 3.11+. No required third-party packages.
# For reading PDFs, have either poppler (`pdftotext`) or `pip install pypdf`.
cp config.example.toml config.toml        # set keywords, categories, sender details
python -m rdt run -c config.toml          # creates runs/<timestamp>/
```

Each run folder contains:

| File | What it is |
|---|---|
| `report.md` | Ranked papers, chosen contact, and whether a draft was made or skipped (and why) |
| `drafts/*.eml` | One email per paper. Double-click to open as an editable draft in Outlook or Apple Mail, or import into Gmail |
| `contacts.csv` | Every author of every relevant paper, with email, confidence and corresponding-author flag |
| `papers.json` | Full metadata, relevance score and matched terms |

Useful flags: `--days 3`, `--keywords "graph neural network, fraud"`, `--dry-run` (doesn't update the contact log).

## How each stage works

**Discover.** arXiv is searched with its query API (keywords OR'd over title and abstract, AND'd with your categories). bioRxiv and medRxiv have no keyword search, so RDT pages through everything posted in the date window, filters by subject category, and keeps the latest version of each DOI. Cross-posted duplicates are merged on title.

**Rank.** Each paper gets a 0–1 score: half for how many of your keywords appear, half for how strongly (title hits count 3×, with diminishing returns). `exclude` terms drop a paper; `must_have` terms are required. Matched terms are recorded and used in the email.

**Extract contacts.** Pre-print APIs rarely include emails, so RDT downloads the PDF and reads the first pages. It handles plain addresses, grouped forms like `{alice,bob}@uni.edu`, obfuscations like `name [at] uni [dot] edu`, and "Corresponding author:" lines. Each email is matched to one author by comparing it with their name:

- **high**: first and last name (`priya.raman@`, `praman@`)
- **medium**: surname only, or a corresponding-author email tied to the corresponding author from metadata
- **low**: initials only (`wz2201@`)

The contact chosen is the corresponding author if they have an email, otherwise the first author with one. Papers below `min_confidence` are skipped and listed in the report, not guessed.

**Compose.** `template` mode fills a customisable `string.Template`. `llm` mode sends the title, abstract and your purpose to Claude (set `ANTHROPIC_API_KEY`). Claude is told to cite one specific idea from the abstract and not to invent anything. If the call fails, RDT falls back to the template.

**Stage and send.** Drafts are written as `.eml` files and nothing is sent by default. To send through SMTP after reviewing:

```bash
export RDT_SMTP_HOST=smtp.office365.com RDT_SMTP_PORT=587 RDT_SMTP_USER=you@firm.com RDT_SMTP_PASSWORD=...
python -m rdt send runs/20261117-094610     # lists recipients, asks you to type "send"
```

Delete any `.eml` you don't want to send before running `send`.

## Guardrails built in

- **Contact log** (`rdt_contact_log.json`): records every person drafted or emailed, so the same author or paper isn't picked up again in later runs.
- **Do-not-contact list**: block single addresses or whole domains (`"@example.edu"`).
- **Per-run cap** (`max_emails_per_run`), **confidence threshold**, and **review-before-send** by default.
- **Polite crawling**: identifying User-Agent with your email, 3 s between arXiv requests (as arXiv asks), backoff on 429/5xx.
- Each email has a one-time-contact note and an easy opt-out line. Keep these if you send commercially (CAN-SPAM; GDPR "legitimate interest" for EU authors).

## Scheduling

To run weekly, use cron (`0 7 * * 1 cd /path/to/rdt && python -m rdt run -c config.toml`) or a Windows scheduled task. The contact log keeps weekly runs from repeating outreach.

## Extending

- **New source** (e.g. SSRN, chemRxiv, Research Square): add `rdt/sources/<name>.py` that returns `list[Paper]`, then call it in `agent.discover()`.
- **Better ranking**: swap `relevance.score` for an embedding model or an LLM relevance judge. The rest of the pipeline only reads `paper.relevance`.
- **Mail integration**: replace `outbox.write_eml` with a Gmail/Outlook API call to create drafts directly in your mailbox.

## Tests

```bash
python -m unittest discover tests      # offline; uses fixture feeds and generated PDFs (needs reportlab)
```
