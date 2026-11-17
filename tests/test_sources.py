"""Offline tests for configurable sources (no third-party packages needed)."""
import tempfile
import unittest
from datetime import date, timedelta
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

from rdt.agent import discover, load_config
from rdt.composer import compose_template
from rdt.models import Author
from rdt.sources import configured_sources, fetch, rss

TODAY = date.today()
RFC_NOW = format_datetime(datetime.now(timezone.utc))
OLD = (TODAY - timedelta(days=60)).isoformat()

RSS2 = f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Example</title>
<item>
  <title>Graph Neural Networks for &lt;i&gt;Fraud&lt;/i&gt; Detection</title>
  <link>https://example.org/papers/1</link>
  <guid>10.1234/abc.1</guid>
  <description>&lt;p&gt;We study fraud detection with GNNs.&lt;/p&gt;</description>
  <pubDate>{RFC_NOW}</pubDate>
  <dc:creator>Priya Raman, Wei Zhang and Ana Lopez</dc:creator>
  <category>Finance</category>
  <enclosure url="https://example.org/papers/1.pdf" type="application/pdf" length="1"/>
</item>
<item>
  <title>An Old Paper</title>
  <link>https://example.org/papers/2</link>
  <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
</item>
</channel></rss>""".encode()

ATOM = f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <id>urn:x:42</id>
  <title>Credit Risk with LLMs</title>
  <link rel="alternate" href="https://example.org/42"/>
  <link rel="related" type="application/pdf" href="https://example.org/42/download"/>
  <summary>Abstract text.</summary>
  <published>{TODAY.isoformat()}T09:00:00Z</published>
  <author><name>Jane  Doe</name></author>
  <author><name>John Roe</name></author>
</entry>
</feed>""".encode()

RDF = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
<item rdf:about="https://journal.example/articles/s41-1">
  <title>Journal Article</title>
  <dc:creator>Smith, J.</dc:creator>
  <dc:creator>Doe, Alice B.</dc:creator>
  <dc:identifier>doi:10.1038/s41-1</dc:identifier>
  <prism:publicationDate>{TODAY.isoformat()}</prism:publicationDate>
</item>
</rdf:RDF>""".encode()


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, max_bytes=0):
        return self.pages[url]


class TestRss(unittest.TestCase):
    def test_rss2(self):
        spec = {"name": "ex", "type": "rss", "url": "u"}
        papers = rss.search(FakeHttp({"u": RSS2}), spec, [], days=7)
        self.assertEqual(len(papers), 1)  # old item dropped by date window
        p = papers[0]
        self.assertEqual(p.title, "Graph Neural Networks for Fraud Detection")
        self.assertEqual(p.abstract, "We study fraud detection with GNNs.")
        self.assertEqual([a.name for a in p.authors], ["Priya Raman", "Wei Zhang", "Ana Lopez"])
        self.assertEqual(p.pdf_url, "https://example.org/papers/1.pdf")
        self.assertEqual((p.id, p.source, p.published), ("10.1234/abc.1", "ex", TODAY.isoformat()))
        self.assertEqual(p.categories, ["finance"])

    def test_atom(self):
        [p] = rss.parse_feed(ATOM, "a")
        self.assertEqual((p.id, p.url, p.pdf_url), ("urn:x:42", "https://example.org/42", "https://example.org/42/download"))
        self.assertEqual([a.name for a in p.authors], ["Jane Doe", "John Roe"])

    def test_rdf_and_pdf_template(self):
        [p] = rss.parse_feed(RDF, "j", pdf_url_template="{url}.pdf")
        self.assertEqual(p.url, "https://journal.example/articles/s41-1")
        self.assertEqual(p.pdf_url, "https://journal.example/articles/s41-1.pdf")
        self.assertEqual([a.name for a in p.authors], ["J. Smith", "Alice B. Doe"])
        self.assertEqual(p.published, TODAY.isoformat())

    def test_category_filter(self):
        spec = {"name": "ex", "type": "rss", "url": "u", "categories": ["Biology"]}
        self.assertEqual(rss.search(FakeHttp({"u": RSS2}), spec, [], days=7), [])


class TestConfig(unittest.TestCase):
    def test_no_implicit_defaults(self):
        self.assertEqual(configured_sources(None), [])
        self.assertEqual(configured_sources([]), [])
        with self.assertRaisesRegex(ValueError, "No sources configured"):
            discover({"search": {}}, FakeHttp({}))

    def test_validation(self):
        for bad, msg in [([{"type": "nope"}], "unknown type"),
                         ([{"type": "rss", "url": "u"}], "needs a 'name'"),
                         ([{"type": "rss", "name": "x"}], "needs a 'url'"),
                         ([{"type": "python", "name": "x"}], "needs 'function'"),
                         ([{"type": "arxiv"}, {"type": "arxiv"}], "duplicate")]:
            with self.assertRaisesRegex(ValueError, msg):
                configured_sources(bad)

    def test_legacy_tables_and_disabled(self):
        specs = configured_sources({"arxiv": {"categories": ["cs.LG"]}, "biorxiv": {"enabled": False}})
        self.assertEqual([(s["name"], s["type"]) for s in specs], [("arxiv", "arxiv")])

    def test_only_filter(self):
        srcs = [{"type": "arxiv"}, {"type": "rss", "name": "f", "url": "u"}]
        self.assertEqual([s["name"] for s in configured_sources(srcs, ["f"])], ["f"])
        with self.assertRaisesRegex(ValueError, "no enabled source named zzz"):
            configured_sources(srcs, ["zzz"])


class TestPythonSource(unittest.TestCase):
    def test_custom_function_and_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "mine.py").write_text(
                "def search(http, spec, keywords, days):\n"
                "    return [{'id': 'X1', 'title': 'Custom  Paper', 'authors': ['Jane Doe', {'name': 'Bo Li', 'email': 'bo@x.edu'}],\n"
                "             'published': spec['when'], 'url': 'https://x/1', 'pdf_url': 'https://x/1.pdf'}]\n")
            Path(tmp, "config.toml").write_text(
                f'[search]\nkeywords = ["paper"]\n\n'
                f'[[sources]]\nname = "mine"\ntype = "python"\nfunction = "mine.py:search"\n'
                f'label = "My Index"\nwhen = "{TODAY.isoformat()}"\n')
            cfg = load_config(Path(tmp, "config.toml"))
            [p] = discover(cfg, FakeHttp({}))
        self.assertEqual((p.key, p.title, p.source_name), ("mine:X1", "Custom Paper", "My Index"))
        self.assertEqual(p.authors[1].email, "bo@x.edu")
        d = compose_template(p, Author("Jane Doe", "jane@x.edu"), {"name": "Me", "email": "me@x.com"}, None, None)
        self.assertIn("posted on My Index", d.body)

    def test_failing_source_does_not_stop_others(self):
        srcs = [{"type": "rss", "name": "broken", "url": "missing"},
                {"type": "rss", "name": "ok", "url": "u"}]
        papers = discover({"search": {"days": 7}, "sources": srcs}, FakeHttp({"u": RSS2}))
        self.assertEqual([p.source for p in papers], ["ok"])

    def test_bad_config_fails_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.toml").write_text('[[sources]]\ntype = "rss"\nname = "x"\n')
            with self.assertRaisesRegex(ValueError, "needs a 'url'"):
                load_config(Path(tmp, "config.toml"))


if __name__ == "__main__":
    unittest.main()
