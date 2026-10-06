from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

from .security import validate_public_http_url


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_document(html: str, url: str, max_chars: int = 50000) -> dict:
    validate_public_http_url(url)
    title = ""
    published = ""
    author = ""
    text = ""
    extraction = "beautifulsoup"

    try:
        import trafilatura
        result = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_links=False,
            include_images=False,
            include_tables=True,
            favor_precision=True,
        )
        if result:
            import json
            data = json.loads(result)
            text = str(data.get("text") or "").strip()
            title = clean_space(data.get("title") or "")
            published = clean_space(data.get("date") or "")
            author = clean_space(data.get("author") or "")
            extraction = "trafilatura"
    except Exception:
        pass

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    if not title:
        title = clean_space(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not published:
        for attrs in (
            {"property": "article:published_time"},
            {"name": "date"},
            {"name": "pubdate"},
            {"itemprop": "datePublished"},
        ):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                published = clean_space(tag.get("content"))
                break
    if not text:
        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()
        text = "\n".join(clean_space(line) for line in soup.get_text("\n").splitlines() if clean_space(line))

    cap = max(1000, min(int(max_chars or 50000), 250000))
    truncated = len(text) > cap
    return {
        "url": url,
        "title": title,
        "author": author,
        "published": published,
        "retrieved": datetime.now(timezone.utc).isoformat(),
        "extraction": extraction,
        "text": text[:cap],
        "text_chars": len(text),
        "truncated": truncated,
    }


def extract_links(html: str, base_url: str, selector: str = "", same_domain: bool = False, extensions: str = "") -> list[dict]:
    validate_public_http_url(base_url)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    nodes = soup.select(selector) if selector.strip() else soup.find_all("a")
    base_host = (urlparse(base_url).hostname or "").lower()
    wanted = {"." + x.strip().lower().lstrip(".") for x in extensions.split(",") if x.strip()}
    out: list[dict] = []
    seen: set[str] = set()
    for node in nodes:
        href = node.get("href") if hasattr(node, "get") else None
        if not href:
            continue
        absolute, _frag = urldefrag(urljoin(base_url, href))
        try:
            validate_public_http_url(absolute)
        except Exception:
            continue
        parsed = urlparse(absolute)
        if same_domain and (parsed.hostname or "").lower() != base_host:
            continue
        if wanted and not any(parsed.path.lower().endswith(ext) for ext in wanted):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append({
            "url": absolute,
            "text": clean_space(node.get_text(" ", strip=True))[:500],
            "title": clean_space(node.get("title") or "")[:500],
        })
    return out


def looks_like_challenge(title: str, text: str) -> bool:
    sample = (title + "\n" + text[:8000]).lower()
    markers = (
        "verify you are human", "checking your browser", "just a moment", "captcha",
        "access denied", "unusual traffic", "security check", "enable javascript and cookies",
    )
    return any(marker in sample for marker in markers)
