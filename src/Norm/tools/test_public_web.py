from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from norm_runtime.public_web import (
    PublicWebError,
    _decode_bing_news_url,
    _parse_reader_markdown,
    _reader_proxy_safe,
    extract_readable_html,
    validate_public_url,
)
from norm_runtime.file_tool_executor import FileToolExecutor
from norm_runtime.prompt_worker import PromptWorker


def public_dns(host, port, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


class PublicWebTests(unittest.TestCase):
    @patch("norm_runtime.public_web.socket.getaddrinfo", side_effect=public_dns)
    def test_public_https_allowed(self, _resolver):
        result = validate_public_url("https://example.com/news?id=4#fragment")
        self.assertEqual(result["scheme"], "https")
        self.assertEqual(result["host"], "example.com")
        self.assertEqual(result["port"], 443)
        self.assertNotIn("#", result["url"])

    def test_local_private_and_tailscale_targets_rejected(self):
        blocked = [
            "http://127.0.0.1/",
            "http://192.168.1.20/",
            "http://169.254.169.254/latest/meta-data/",
            "http://" + "100." + "64.0.1/",
            "http://[::1]/",
            "http://localhost/",
            "https://host.example." + "ts.net/",
            "file:///C:/Windows/System32/drivers/etc/hosts",
            "https://user:password@example.com/",
            "https://example.com:8443/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(PublicWebError):
                    validate_public_url(url)

    @patch("norm_runtime.public_web.socket.getaddrinfo")
    def test_dns_rebinding_private_answer_rejected(self, resolver):
        resolver.return_value = [(2, 1, 6, "", ("10.23.4.5", 443))]
        with self.assertRaisesRegex(PublicWebError, "non-public"):
            validate_public_url("https://example.com/")

    def test_readable_html_extracts_title_text_and_links_without_script_nav(self):
        doc = """
        <html>
          <head><title>Example Story</title><meta name="description" content="Short summary"></head>
          <body>
            <nav>Noise menu</nav>
            <article>
              <h1>Example Story</h1>
              <p>First paragraph with useful text.</p>
              <p>Second paragraph.</p>
              <a href="/more">Read more</a>
            </article>
            <script>secretNoise()</script>
            <footer>Footer noise</footer>
          </body>
        </html>
        """
        result = extract_readable_html(doc, "https://example.com/story")
        self.assertEqual(result["title"], "Example Story")
        self.assertEqual(result["description"], "Short summary")
        self.assertIn("First paragraph with useful text.", result["text"])
        self.assertIn("Second paragraph.", result["text"])
        self.assertNotIn("Noise menu", result["text"])
        self.assertNotIn("secretNoise", result["text"])
        self.assertNotIn("Footer noise", result["text"])
        self.assertEqual(result["links"][0]["url"], "https://example.com/more")

    def test_bing_news_redirect_url_is_decoded(self):
        encoded = (
            "http://www.bing.com/news/apiclick.aspx?"
            "ref=FexRss&url=https%3A%2F%2Fexample.com%2Farticle%3Fx%3D1"
        )
        self.assertEqual(
            _decode_bing_news_url(encoded),
            "https://example.com/article?x=1",
        )

    def test_reader_proxy_rejects_sensitive_query_keys(self):
        self.assertTrue(_reader_proxy_safe("https://example.com/article?id=12"))
        self.assertFalse(_reader_proxy_safe("https://example.com/article?token=secret"))
        self.assertFalse(_reader_proxy_safe("https://example.com/article?x-amz-signature=abc"))

    def test_reader_markdown_parses_article_metadata_and_text(self):
        payload = """Title: Example Article
URL Source: https://example.com/article
Published Time: 2026-10-05T01:00:00Z

Markdown Content:
# Example Article

Useful paragraph.

![Image 1](https://example.com/image.jpg)

[Source link](https://example.com/source)
"""
        parsed = _parse_reader_markdown(payload, "https://example.com/article")
        self.assertEqual(parsed["title"], "Example Article")
        self.assertEqual(parsed["source_url"], "https://example.com/article")
        self.assertEqual(parsed["published"], "2026-10-05T01:00:00Z")
        self.assertIn("Useful paragraph.", parsed["text"])
        self.assertNotIn("![Image", parsed["text"])
        self.assertEqual(parsed["links"][0]["url"], "https://example.com/source")

    def test_web_tools_are_only_advertised_when_enabled(self):
        common = dict(
            allowed_roots=[str(ROOT)],
            backup_root=str(ROOT / "state" / "file-backups"),
            audit_log=str(ROOT / "logs" / "tool-audit.jsonl"),
        )
        disabled = FileToolExecutor(**common, public_web_enabled=False)
        enabled = FileToolExecutor(**common, public_web_enabled=True)
        disabled_names = {row["function"]["name"] for row in disabled.schemas()}
        enabled_names = {row["function"]["name"] for row in enabled.schemas()}
        self.assertNotIn("web_search", disabled_names)
        self.assertNotIn("web_fetch", disabled_names)
        self.assertIn("web_search", enabled_names)
        self.assertIn("web_fetch", enabled_names)

    def test_web_tools_require_verification_checkin(self):
        self.assertTrue(PromptWorker._information_tool_requires_verification("web_search"))
        self.assertTrue(PromptWorker._information_tool_requires_verification("web_fetch"))

    def test_runtime_config_enables_public_web(self):
        text = (ROOT / "config" / "runtime.json").read_text(encoding="utf-8")
        self.assertIn('"public_web"', text)
        self.assertIn('"enabled": true', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
