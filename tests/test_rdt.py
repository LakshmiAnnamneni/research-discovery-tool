import email
import email.policy
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rdt import contacts, relevance
from rdt.agent import run
from rdt.composer import compose_template
from rdt.models import Author, Paper
from rdt.sources import arxiv, biorxiv

FIX = Path(__file__).parent / "fixtures"
TODAY = date.today().isoformat()


def fixture(name: str) -> bytes:
    return (FIX / name).read_text().replace("{TODAY}", TODAY).encode()


def make_pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 18
    c.save()
    return buf.getvalue()


ARXIV_PDF = make_pdf([
    "Retrieval-Augmented Large Language Models for Credit Risk Assessment",
    "Priya Raman*, Marcus J. Lee, Wei Zhang",
    "University of Florida",
    "{praman, mlee}@ufl.edu   wz2201@columbia.edu",
    "*Corresponding author: praman@ufl.edu",
])
BIORXIV_PDF = make_pdf([
    "Machine learning predicts antibiotic resistance from bacterial genomes",
    "Chidi Okafor, Linh T. Nguyen, Erik Svensson",
    "Correspondence: erik.svensson@ki.se",
])


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url, max_bytes=None):
        self.calls.append(url)
        if "export.arxiv.org" in url:
            return fixture("arxiv_feed.xml")
        if "api.biorxiv.org" in url:
            return fixture("biorxiv_page.json")
        if "arxiv.org/pdf/2611.01234" in url:
            return ARXIV_PDF
        if "biorxiv.org/content/10.1101/2026.11.10.123456" in url:
            return BIORXIV_PDF
        raise RuntimeError(f"404 {url}")


class TestSources(unittest.TestCase):
    def test_arxiv_query(self):
        q = arxiv.build_query(["credit risk", "LLM"], ["q-fin.RM"])
        self.assertEqual(q, '(ti:"credit risk" OR abs:"credit risk" OR ti:LLM OR abs:LLM) AND (cat:q-fin.RM)')

    def test_arxiv_parse_and_date_filter(self):
        papers = arxiv.search(FakeHttp(), ["credit risk"], [], days=7)
        self.assertEqual(len(papers), 2)  # 2020 paper dropped
        p = papers[0]
        self.assertEqual(p.id, "2611.01234v1")
        self.assertEqual(p.title, "Retrieval-Augmented Large Language Models for Credit Risk Assessment")
        self.assertEqual(p.pdf_url, "https://arxiv.org/pdf/2611.01234v1")
        self.assertEqual(p.authors[0].affiliation, "University of Florida")

    def test_biorxiv_versions_categories_corresponding(self):
        papers = biorxiv.search(FakeHttp(), "biorxiv", ["Bioinformatics"], days=7)
        self.assertEqual(len(papers), 1)
        p = papers[0]
        self.assertTrue(p.url.endswith("v2"))
        self.assertEqual([a.name for a in p.authors], ["Chidi Okafor", "Linh T. Nguyen", "Erik Svensson"])
        corr = [a for a in p.authors if a.is_corresponding]
        self.assertEqual(corr[0].name, "Erik Svensson")
        self.assertEqual(corr[0].affiliation, "Karolinska Institutet")


class TestRelevance(unittest.TestCase):
    def _p(self, title, abstract):
        return Paper("arxiv", "x", title, abstract, [], TODAY, "u")

    def test_ranking_and_exclusion(self):
        kws = ["credit risk", "large language model"]
        good = self._p("LLMs for Credit Risk", "Large language models for credit risk scoring.")
        weak = self._p("Market microstructure", "We mention credit risk once.")
        bad = self._p("Credit risk survey", "A survey of credit risk with large language models.")
        ranked = relevance.rank([weak, good, bad], kws, 0.3, exclude=["survey"])
        self.assertEqual(ranked[0], good)
        self.assertNotIn(bad, ranked)
        self.assertEqual(good.matched_terms, kws)
        self.assertGreater(good.relevance, weak.relevance)

    def test_must_have(self):
        p = self._p("Credit risk", "No models here.")
        self.assertEqual(relevance.score(p, ["credit risk"], must_have=["language model"]).relevance, 0)


class TestContacts(unittest.TestCase):
    def test_extract_grouped_and_corresponding(self):
        text = "{praman, mlee}@ufl.edu  wz2201@columbia.edu\nCorresponding author: praman@ufl.edu\nnoreply@ufl.edu"
        emails, corr = contacts.extract_emails(text)
        self.assertEqual(emails, ["praman@ufl.edu", "mlee@ufl.edu", "wz2201@columbia.edu"])
        self.assertEqual(corr, {"praman@ufl.edu"})

    def test_obfuscated(self):
        emails, _ = contacts.extract_emails("contact: jane.doe [at] mit.edu, bob (at) cs [dot] stanford [dot] edu")
        self.assertEqual(emails, ["jane.doe@mit.edu", "bob@cs.stanford.edu"])

    def test_match_scores(self):
        self.assertEqual(contacts.match_score("priya.raman@ufl.edu", "Priya Raman"), 3)
        self.assertEqual(contacts.match_score("praman@ufl.edu", "Priya Raman"), 3)
        self.assertEqual(contacts.match_score("raman@ufl.edu", "Priya Raman"), 2)
        self.assertEqual(contacts.match_score("mjl@ufl.edu", "Marcus J. Lee"), 1)
        self.assertEqual(contacts.match_score("zz9@ufl.edu", "Priya Raman"), 0)
        self.assertEqual(contacts.match_score("jose.munoz@uam.es", "José Muñoz"), 3)

    def test_attribute_one_to_one(self):
        authors = [Author("Priya Raman"), Author("Marcus J. Lee"), Author("Wei Zhang")]
        contacts.attribute(authors, ["praman@ufl.edu", "mlee@ufl.edu", "wz2201@columbia.edu"], {"praman@ufl.edu"})
        self.assertEqual(authors[0].email, "praman@ufl.edu")
        self.assertTrue(authors[0].is_corresponding)
        self.assertEqual(authors[1].email, "mlee@ufl.edu")
        # "wz2201" matches initials only -> attributed, but flagged low confidence
        self.assertEqual((authors[2].email, authors[2].email_confidence), ("wz2201@columbia.edu", "low"))

    def test_pdf_text(self):
        self.assertIn("Corresponding author", contacts.pdf_to_text(ARXIV_PDF))


class TestComposer(unittest.TestCase):
    def test_template(self):
        p = Paper("arxiv", "1", "A Title", "abs", [], "2026-11-16", "https://arxiv.org/abs/1",
                  matched_terms=["credit risk", "LLMs"])
        a = Author("Priya Raman", email="praman@ufl.edu", email_confidence="high")
        d = compose_template(p, a, {"name": "L. A.", "email": "me@firm.com", "purpose": "I run a consultancy.",
                                    "ask": "set up a 20-minute call"})
        self.assertEqual(d.subject, 'Your recent pre-print: "A Title"')
        self.assertIn("Dear Dr. Raman,", d.body)
        self.assertIn("credit risk and LLMs", d.body)
        self.assertIn("set up a 20-minute call", d.body)
        self.assertNotIn("$", d.body)


class TestEndToEnd(unittest.TestCase):
    def test_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "search": {"keywords": ["credit risk", "large language model", "antibiotic resistance",
                                        "machine learning"], "days": 7, "min_score": 0.2},
                "sources": {"arxiv": {"enabled": True}, "biorxiv": {"enabled": True, "categories": ["bioinformatics"]}},
                "contacts": {"min_confidence": "medium"},
                "sender": {"name": "Test Sender", "email": "me@firm.com", "purpose": "Hello."},
                "outreach": {"log_path": f"{tmp}/log.json", "max_emails_per_run": 5},
            }
            res = run(cfg, tmp, http=FakeHttp())
            titles = [p.title for p in res.papers]
            self.assertNotIn("A Survey of Galaxy Rotation Curves", titles)
            to = sorted(d.to_email for d in res.drafts)
            self.assertEqual(to, ["erik.svensson@ki.se", "praman@ufl.edu"])

            emls = list((res.out_dir / "drafts").glob("*.eml"))
            self.assertEqual(len(emls), 2)
            m = email.message_from_bytes(emls[0].read_bytes(), policy=email.policy.default)
            self.assertEqual(m["X-Unsent"], "1")
            self.assertTrue((res.out_dir / "report.md").read_text().count("draft ready") == 2)
            self.assertIn("praman@ufl.edu", (res.out_dir / "contacts.csv").read_text())

            # second run: same papers/people are not drafted again
            res2 = run(cfg, Path(tmp) / "second", http=FakeHttp())
            self.assertEqual(res2.drafts, [])
            log = json.loads(Path(f"{tmp}/log.json").read_text())
            self.assertIn("praman@ufl.edu", log["contacted"])

    def test_do_not_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"search": {"keywords": ["credit risk"], "min_score": 0.2},
                   "sources": {"arxiv": {"enabled": True}},
                   "outreach": {"log_path": f"{tmp}/log.json", "do_not_contact": ["@ufl.edu"]}}
            res = run(cfg, tmp, http=FakeHttp(), dry_run=True)
            self.assertEqual(res.drafts, [])
            self.assertIn("do-not-contact list", dict(res.skipped).values())
            self.assertFalse(Path(f"{tmp}/log.json").exists())


if __name__ == "__main__":
    unittest.main()
