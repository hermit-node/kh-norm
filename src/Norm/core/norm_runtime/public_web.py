from __future__ import annotations

import html
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib import parse, request
from urllib.error import HTTPError, URLError


class PublicWebError(RuntimeError):
    pass


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
}
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".lan",
    ".home.arpa",
    ".ts.net",
)
_ALLOWED_PORTS = {80, 443}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "pre", "td", "th", "br",
}
_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "canvas", "template",
    "form", "nav", "footer", "dialog",
}


def _host_is_blocked(host: str) -> bool:
    lowered = str(host or "").strip().rstrip(".").casefold()
    return (
        not lowered
        or lowered in _BLOCKED_HOSTS
        or any(lowered.endswith(suffix) for suffix in _BLOCKED_SUFFIXES)
    )


def _public_ips(host: str, port: int) -> list[str]:
    if _host_is_blocked(host):
        raise PublicWebError(f"blocked host: {host}")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PublicWebError(f"could not resolve public host {host}: {exc}") from exc

    seen: list[str] = []
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise PublicWebError(f"unrecognized resolved address for {host}: {raw}") from exc
        if ip in _CGNAT or not ip.is_global:
            raise PublicWebError(f"blocked non-public address for {host}: {ip}")
        text = str(ip)
        if text not in seen:
            seen.append(text)
    if not seen:
        raise PublicWebError(f"host resolved to no usable public addresses: {host}")
    return seen


def validate_public_url(url: str) -> dict:
    value = str(url or "").strip()
    if not value:
        raise PublicWebError("url is required")
    parsed = parse.urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise PublicWebError("only http/https public web URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise PublicWebError("embedded URL credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise PublicWebError("URL hostname is required")
    try:
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    except ValueError as exc:
        raise PublicWebError(f"invalid URL port: {exc}") from exc
    if port not in _ALLOWED_PORTS:
        raise PublicWebError(f"only public web ports 80/443 are allowed; got {port}")
    addresses = _public_ips(host, port)
    normalized = parse.urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc,
        parsed.path or "/",
        parsed.query,
        "",
    ))
    return {
        "url": normalized,
        "scheme": parsed.scheme.casefold(),
        "host": host,
        "port": port,
        "resolved_public_addresses": addresses,
    }


class _PublicRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    content_type: str
    charset: str
    body: bytes
    body_truncated: bool


def fetch_public_bytes(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    max_bytes: int = 5_242_880,
    user_agent: str = "Norm/0.53.19 public-web",
    accept: str = "text/html,application/xhtml+xml,text/plain,application/xml,text/xml,application/rss+xml;q=0.9,*/*;q=0.1",
) -> FetchResult:
    initial = validate_public_url(url)
    opener = request.build_opener(_PublicRedirectHandler())
    req = request.Request(
        initial["url"],
        headers={
            "User-Agent": user_agent,
            "Accept": accept,
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    cap = max(16_384, min(int(max_bytes or 5_242_880), 16_777_216))
    try:
        with opener.open(req, timeout=max(1.0, min(float(timeout_seconds), 60.0))) as response:
            final_url = str(response.geturl())
            validate_public_url(final_url)
            content_type = str(response.headers.get_content_type() or "application/octet-stream").casefold()
            charset = str(response.headers.get_content_charset() or "utf-8")
            data = response.read(cap + 1)
            truncated = len(data) > cap
            data = data[:cap]
            return FetchResult(
                requested_url=initial["url"],
                final_url=final_url,
                status=int(getattr(response, "status", 200) or 200),
                content_type=content_type,
                charset=charset,
                body=data,
                body_truncated=truncated,
            )
    except HTTPError as exc:
        raise PublicWebError(f"HTTP {exc.code} while fetching {initial['url']}") from exc
    except URLError as exc:
        raise PublicWebError(f"public web fetch failed for {initial['url']}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise PublicWebError(f"public web fetch timed out for {initial['url']}") from exc


class _ReadableHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta_description = ""
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._anchor_href = ""
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        attrs_dict = {str(k).casefold(): str(v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (attrs_dict.get("name") or attrs_dict.get("property") or "").casefold()
            if key in {"description", "og:description", "twitter:description"} and not self.meta_description:
                self.meta_description = attrs_dict.get("content", "").strip()
        if tag == "a":
            href = attrs_dict.get("href", "").strip()
            self._anchor_href = href
            self._anchor_parts = []
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in _SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = False
        if tag == "a":
            href = self._anchor_href
            anchor_text = " ".join(" ".join(self._anchor_parts).split())
            if href:
                absolute = parse.urljoin(self.base_url, href)
                parsed = parse.urlsplit(absolute)
                if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
                    self.links.append({"text": anchor_text[:500], "url": absolute})
            self._anchor_href = ""
            self._anchor_parts = []
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        if self._anchor_href:
            self._anchor_parts.append(text)
        self.parts.append(text + " ")


def _normalize_readable_text(raw: str) -> str:
    raw = html.unescape(raw)
    lines: list[str] = []
    seen_blank = False
    for line in raw.replace("\r", "\n").split("\n"):
        clean = " ".join(line.split()).strip()
        if clean:
            lines.append(clean)
            seen_blank = False
        elif lines and not seen_blank:
            lines.append("")
            seen_blank = True
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def extract_readable_html(document: str, base_url: str) -> dict:
    parser = _ReadableHTMLParser(base_url)
    parser.feed(document)
    parser.close()
    title = " ".join(" ".join(parser.title_parts).split()).strip()
    text = _normalize_readable_text("".join(parser.parts))
    dedup_links: list[dict[str, str]] = []
    seen = set()
    for link in parser.links:
        key = (link["url"], link["text"])
        if key in seen:
            continue
        seen.add(key)
        dedup_links.append(link)
        if len(dedup_links) >= 100:
            break
    return {
        "title": title,
        "description": parser.meta_description,
        "text": text,
        "links": dedup_links,
    }


def fetch_readable(
    url: str,
    *,
    start_char: int = 0,
    max_chars: int = 30_000,
    max_download_bytes: int = 5_242_880,
    timeout_seconds: float = 20.0,
    include_links: bool = True,
) -> dict:
    fetched = fetch_public_bytes(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_download_bytes,
    )
    start = max(0, int(start_char or 0))
    cap = max(1_000, min(int(max_chars or 30_000), 100_000))
    try:
        decoded = fetched.body.decode(fetched.charset or "utf-8", errors="replace")
    except LookupError:
        decoded = fetched.body.decode("utf-8", errors="replace")

    html_types = {"text/html", "application/xhtml+xml"}
    if fetched.content_type in html_types or "<html" in decoded[:2000].casefold():
        extracted = extract_readable_html(decoded, fetched.final_url)
        full_text = extracted["text"]
        title = extracted["title"]
        description = extracted["description"]
        links = extracted["links"] if include_links else []
        extraction = "readable_html"
    elif fetched.content_type.startswith("text/") or fetched.content_type in {
        "application/xml", "text/xml", "application/rss+xml", "application/json"
    }:
        full_text = _normalize_readable_text(decoded)
        title = ""
        description = ""
        links = []
        extraction = "plain_text"
    else:
        raise PublicWebError(
            f"unsupported content type for readable text: {fetched.content_type}"
        )

    end = min(len(full_text), start + cap)
    return {
        "requested_url": fetched.requested_url,
        "final_url": fetched.final_url,
        "status": fetched.status,
        "content_type": fetched.content_type,
        "extraction": extraction,
        "title": title,
        "description": description,
        "text": full_text[start:end],
        "start_char": start,
        "next_char": end,
        "total_chars": len(full_text),
        "truncated": bool(fetched.body_truncated or end < len(full_text)),
        "download_truncated": fetched.body_truncated,
        "links": links,
    }


def _decode_bing_news_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = parse.urlsplit(value)
    direct = (parse.parse_qs(parsed.query).get("url") or [""])[0].strip()
    return direct or value



_SENSITIVE_QUERY_KEYS = {
    "access_token", "auth", "authorization", "code", "credential", "key",
    "password", "secret", "sig", "signature", "token", "x-amz-signature",
}


def _reader_proxy_safe(url: str) -> bool:
    parsed = parse.urlsplit(str(url or ""))
    for key in parse.parse_qs(parsed.query, keep_blank_values=True):
        lowered = key.casefold().replace("-", "_")
        if lowered in _SENSITIVE_QUERY_KEYS:
            return False
        if any(piece in lowered for piece in ("token", "secret", "signature", "password", "auth")):
            return False
    return True


def _parse_reader_markdown(body: str, original_url: str) -> dict:
    text = str(body or "").replace("\r\n", "\n")
    title = ""
    source_url = original_url
    published = ""
    marker_text = "Markdown Content:"
    lines = text.split("\n")
    content_index = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Title:") and not title:
            title = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("URL Source:"):
            candidate = stripped.split(":", 1)[1].strip()
            if candidate:
                source_url = candidate
        elif stripped.startswith("Published Time:"):
            published = stripped.split(":", 1)[1].strip()
        elif stripped == marker_text:
            content_index = index + 1
            break
    content = "\n".join(lines[content_index:] if content_index is not None else lines).strip()
    content = re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$", "", content)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    links: list[dict[str, str]] = []
    seen = set()
    for label, href in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", content):
        key = (href, label)
        if key in seen:
            continue
        seen.add(key)
        links.append({"text": " ".join(label.split())[:500], "url": href})
        if len(links) >= 100:
            break
    return {
        "title": title,
        "source_url": source_url,
        "published": published,
        "text": content,
        "links": links,
    }


def fetch_reader_readable(
    url: str,
    *,
    reader_service_url: str = "https://r.jina.ai/",
    start_char: int = 0,
    max_chars: int = 30_000,
    max_download_bytes: int = 5_242_880,
    timeout_seconds: float = 30.0,
    include_links: bool = True,
) -> dict:
    original = validate_public_url(url)["url"]
    if not _reader_proxy_safe(original):
        raise PublicWebError("reader fallback refused URL with sensitive-looking query parameters")

    base = str(reader_service_url or "https://r.jina.ai/").strip()
    if not base.endswith("/"):
        base += "/"
    validate_public_url(base)
    reader_url = base + original
    fetched = fetch_public_bytes(
        reader_url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_download_bytes,
        accept="text/plain,text/markdown,*/*;q=0.1",
    )
    try:
        decoded = fetched.body.decode(fetched.charset or "utf-8", errors="replace")
    except LookupError:
        decoded = fetched.body.decode("utf-8", errors="replace")
    parsed_reader = _parse_reader_markdown(decoded, original)
    full_text = parsed_reader["text"]
    start = max(0, int(start_char or 0))
    cap = max(1_000, min(int(max_chars or 30_000), 100_000))
    end = min(len(full_text), start + cap)
    return {
        "requested_url": original,
        "final_url": parsed_reader["source_url"] or original,
        "status": fetched.status,
        "content_type": fetched.content_type,
        "extraction": "reader_markdown",
        "text_format": "markdown",
        "reader_used": True,
        "reader_service": parse.urlsplit(base).hostname or base,
        "title": parsed_reader["title"],
        "description": "",
        "published": parsed_reader["published"],
        "text": full_text[start:end],
        "start_char": start,
        "next_char": end,
        "total_chars": len(full_text),
        "truncated": bool(fetched.body_truncated or end < len(full_text)),
        "download_truncated": fetched.body_truncated,
        "links": parsed_reader["links"] if include_links else [],
    }


def search_web(
    query: str,
    *,
    mode: str = "web",
    max_results: int = 10,
    freshness_days: int | None = None,
    timeout_seconds: float = 20.0,
) -> dict:
    q = " ".join(str(query or "").split()).strip()
    if not q:
        raise PublicWebError("search query is required")
    cap = max(1, min(int(max_results or 10), 20))
    mode = str(mode or "web").strip().casefold()
    if mode not in {"web", "news"}:
        raise PublicWebError("search mode must be web or news")

    if mode == "news":
        url = "https://www.bing.com/news/search?" + parse.urlencode({"q": q, "format": "RSS"})
        fetched = fetch_public_bytes(
            url,
            timeout_seconds=timeout_seconds,
            max_bytes=1_500_000,
            accept="application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1",
        )
        try:
            root = ET.fromstring(fetched.body)
        except ET.ParseError as exc:
            raise PublicWebError(f"news search returned invalid RSS/XML: {exc}") from exc
        cutoff = None
        if freshness_days is not None:
            days = max(1, min(int(freshness_days), 30))
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results: list[dict[str, str]] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            bing_link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            source = ""
            for child in list(item):
                if str(child.tag).casefold().endswith("source") and child.text:
                    source = child.text.strip()
                    break
            if not title or not bing_link:
                continue
            link = _decode_bing_news_url(bing_link)
            try:
                published_dt = parsedate_to_datetime(pub) if pub else None
                if published_dt is not None and published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
                if cutoff is not None and published_dt is not None and published_dt < cutoff:
                    continue
                published = published_dt.isoformat() if published_dt is not None else ""
            except Exception:
                published = pub
            snippet = _normalize_readable_text(re.sub(r"<[^>]+>", " ", html.unescape(description)))
            if not source:
                source = parse.urlsplit(link).hostname or ""
            results.append({
                "title": title,
                "url": link,
                "snippet": snippet[:1200],
                "source": source,
                "published": published,
            })
            if len(results) >= cap:
                break
        return {
            "query": q,
            "mode": "news",
            "provider": "bing_news_rss",
            "results": results,
            "result_count": len(results),
        }

    url = "https://www.bing.com/search?" + parse.urlencode({"q": q, "format": "rss"})
    fetched = fetch_public_bytes(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=1_500_000,
        accept="application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1",
    )
    try:
        root = ET.fromstring(fetched.body)
    except ET.ParseError as exc:
        raise PublicWebError(f"web search returned invalid RSS/XML: {exc}") from exc
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not title or not link:
            continue
        snippet = _normalize_readable_text(re.sub(r"<[^>]+>", " ", html.unescape(description)))
        host = parse.urlsplit(link).hostname or ""
        results.append({
            "title": title,
            "url": link,
            "snippet": snippet[:1200],
            "source": host,
            "published": "",
        })
        if len(results) >= cap:
            break
    return {
        "query": q,
        "mode": "web",
        "provider": "bing_rss",
        "results": results,
        "result_count": len(results),
    }
