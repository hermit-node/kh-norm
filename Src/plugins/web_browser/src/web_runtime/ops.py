from __future__ import annotations

import re
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from .browser import browser_context, web_root
from .extract import clean_space, extract_document, extract_links, looks_like_challenge
from .security import safe_filename, validate_public_http_url


def _goto(page, url: str, timeout_ms: int = 30000):
    validate_public_http_url(url)
    response = page.goto(url, wait_until="domcontentloaded", timeout=max(3000, min(int(timeout_ms), 120000)))
    try:
        page.wait_for_load_state("networkidle", timeout=min(8000, max(1500, int(timeout_ms) // 3)))
    except Exception:
        pass
    return response


def open_page(url: str, session: str, headless: bool, timeout_ms: int, max_chars: int) -> dict:
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        response = _goto(page, url, timeout_ms)
        html = page.content()
        doc = extract_document(html, page.url, max_chars=max_chars)
        doc.update({
            "browser": browser_name,
            "status": response.status if response else None,
            "final_url": page.url,
            "blocked_or_challenge": looks_like_challenge(doc.get("title", ""), doc.get("text", "")),
        })
        return doc


def links_page(url: str, session: str, headless: bool, timeout_ms: int, selector: str, same_domain: bool, extensions: str, max_links: int) -> dict:
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        response = _goto(page, url, timeout_ms)
        html = page.content()
        links = extract_links(html, page.url, selector=selector, same_domain=same_domain, extensions=extensions)
        limit = max(1, min(int(max_links or 200), 2000))
        return {
            "url": url,
            "final_url": page.url,
            "title": page.title(),
            "browser": browser_name,
            "status": response.status if response else None,
            "count": min(len(links), limit),
            "total_found": len(links),
            "truncated": len(links) > limit,
            "links": links[:limit],
        }


def crawl_site(start_url: str, session: str, headless: bool, timeout_ms: int, max_pages: int, max_depth: int, same_domain: bool, max_chars_per_page: int) -> dict:
    start_url = validate_public_http_url(start_url)
    page_limit = max(1, min(int(max_pages or 20), 100))
    depth_limit = max(0, min(int(max_depth or 1), 5))
    base_host = (urlparse(start_url).hostname or "").lower()
    queue = deque([(start_url, 0)])
    seen: set[str] = set()
    results: list[dict] = []
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        while queue and len(results) < page_limit:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            try:
                response = _goto(page, url, timeout_ms)
                html = page.content()
                doc = extract_document(html, page.url, max_chars=max_chars_per_page)
                links = extract_links(html, page.url, same_domain=same_domain)
                results.append({
                    "url": url,
                    "final_url": page.url,
                    "depth": depth,
                    "status": response.status if response else None,
                    "title": doc["title"],
                    "published": doc["published"],
                    "text": doc["text"],
                    "text_chars": doc["text_chars"],
                    "truncated": doc["truncated"],
                    "blocked_or_challenge": looks_like_challenge(doc["title"], doc["text"]),
                    "links_found": len(links),
                })
                if depth < depth_limit:
                    for link in links:
                        target = link["url"]
                        host = (urlparse(target).hostname or "").lower()
                        if same_domain and host != base_host:
                            continue
                        if target not in seen:
                            queue.append((target, depth + 1))
            except Exception as exc:
                results.append({"url": url, "depth": depth, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "start_url": start_url,
        "browser": browser_name,
        "pages": results,
        "pages_visited": len(results),
        "queued_remaining": len(queue),
        "max_pages": page_limit,
        "max_depth": depth_limit,
    }


def _content_disposition_name(value: str) -> str:
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", value or "", re.I)
    return urllib.parse.unquote(match.group(1).strip().strip('"')) if match else ""


def _download_stream(url: str, destination: Path, cookies: list[dict], user_agent: str, max_bytes: int) -> dict:
    validate_public_http_url(url)
    cookie_header = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name"))
    headers = {"User-Agent": user_agent or "Mozilla/5.0", "Accept": "*/*"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    request = urllib.request.Request(url, headers=headers)
    limit = max(1024 * 1024, min(int(max_bytes or 134217728), 1024 * 1024 * 1024))
    with urllib.request.urlopen(request, timeout=60) as response:
        final_url = response.geturl()
        validate_public_http_url(final_url)
        name = _content_disposition_name(response.headers.get("Content-Disposition", ""))
        if name:
            destination = destination.with_name(safe_filename(name, destination.name))
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limit:
            raise ValueError(f"download exceeds max_bytes ({declared} > {limit})")
        written = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    handle.close()
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"download exceeded max_bytes ({limit})")
                handle.write(chunk)
        return {
            "url": url,
            "final_url": final_url,
            "path": str(destination),
            "bytes": written,
            "content_type": response.headers.get("Content-Type", ""),
        }


def download_from_page(page_url: str, destination: str, extensions: str, selector: str, max_files: int, max_bytes_per_file: int, session: str, headless: bool, timeout_ms: int) -> dict:
    page_url = validate_public_http_url(page_url)
    root = web_root()
    if destination.strip():
        dest = Path(destination).expanduser().resolve()
        workspace = root.parent.resolve()  # workspace/web -> workspace
        if dest != workspace and not dest.is_relative_to(workspace):
            raise PermissionError(f"destination must be inside Norm workspace: {workspace}")
    else:
        dest = root / "downloads"
    dest.mkdir(parents=True, exist_ok=True)
    file_limit = max(1, min(int(max_files or 20), 100))
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        _goto(page, page_url, timeout_ms)
        links = extract_links(page.content(), page.url, selector=selector, extensions=extensions)
        links = links[:file_limit]
        cookies = context.cookies()
        try:
            user_agent = page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = "Mozilla/5.0"
        downloaded: list[dict] = []
        errors: list[dict] = []
        for index, link in enumerate(links, 1):
            parsed = urlparse(link["url"])
            fallback = f"download-{index}.bin"
            filename = safe_filename(Path(urllib.parse.unquote(parsed.path)).name, fallback)
            try:
                downloaded.append(_download_stream(link["url"], dest / filename, cookies, user_agent, max_bytes_per_file))
            except Exception as exc:
                errors.append({"url": link["url"], "error": f"{type(exc).__name__}: {exc}"})
        return {
            "page_url": page_url,
            "browser": browser_name,
            "destination": str(dest),
            "matched_links": len(links),
            "downloaded": downloaded,
            "errors": errors,
        }


def search_bing(query: str, max_results: int, news: bool, session: str, headless: bool, timeout_ms: int) -> dict:
    query = clean_space(query)
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(max_results or 10), 30))
    if news:
        url = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": query, "FORM": "HDRSC6"})
    else:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": str(limit)})
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        response = _goto(page, url, timeout_ms)
        html = page.content()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict] = []
        if news:
            nodes = soup.select("a.title") or soup.select(".news-card a[href]")
            for node in nodes:
                href = node.get("href")
                if not href:
                    continue
                try:
                    href = validate_public_http_url(urllib.parse.urljoin(page.url, href))
                except Exception:
                    continue
                card = node.find_parent(["div", "li", "article"])
                card_text = clean_space(card.get_text(" ", strip=True) if card else "")
                rows.append({"title": clean_space(node.get_text(" ", strip=True)), "url": href, "snippet": card_text[:1200]})
                if len(rows) >= limit:
                    break
        else:
            for li in soup.select("li.b_algo"):
                node = li.select_one("h2 a[href]")
                if not node:
                    continue
                try:
                    href = validate_public_http_url(node.get("href"))
                except Exception:
                    continue
                snippet_node = li.select_one(".b_caption p") or li.select_one("p")
                rows.append({
                    "title": clean_space(node.get_text(" ", strip=True)),
                    "url": href,
                    "snippet": clean_space(snippet_node.get_text(" ", strip=True) if snippet_node else "")[:1200],
                })
                if len(rows) >= limit:
                    break
        return {
            "query": query,
            "news": bool(news),
            "browser": browser_name,
            "status": response.status if response else None,
            "blocked_or_challenge": looks_like_challenge(page.title(), soup.get_text(" ", strip=True)),
            "count": len(rows),
            "results": rows,
            "search_url": page.url,
        }


def download_direct(url: str, destination: str, filename: str, max_bytes: int, session: str, headless: bool) -> dict:
    url = validate_public_http_url(url)
    root = web_root()
    workspace = root.parent.resolve()
    if destination.strip():
        dest = Path(destination).expanduser().resolve()
        if dest != workspace and not dest.is_relative_to(workspace):
            raise PermissionError(f"destination must be inside Norm workspace: {workspace}")
    else:
        dest = root / "downloads"
    dest.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    fallback = safe_filename(Path(urllib.parse.unquote(parsed.path)).name, "download.bin")
    target = dest / safe_filename(filename, fallback) if filename.strip() else dest / fallback
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        try:
            user_agent = page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = "Mozilla/5.0"
        result = _download_stream(url, target, context.cookies(), user_agent, max_bytes)
        result["browser"] = browser_name
        return result


def latest_updates(query: str, max_results: int, max_chars_per_page: int, session: str, headless: bool, timeout_ms: int) -> dict:
    query = clean_space(query)
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(max_results or 5), 10))
    search_url = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": query, "FORM": "HDRSC6"})
    with browser_context(session=session, headless=headless) as (_p, context, browser_name):
        page = context.pages[0] if context.pages else context.new_page()
        response = _goto(page, search_url, timeout_ms)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.content(), "html.parser")
        rows: list[dict] = []
        nodes = soup.select("a.title") or soup.select(".news-card a[href]")
        for node in nodes:
            href = node.get("href")
            if not href:
                continue
            try:
                href = validate_public_http_url(urllib.parse.urljoin(page.url, href))
            except Exception:
                continue
            card = node.find_parent(["div", "li", "article"])
            rows.append({
                "title": clean_space(node.get_text(" ", strip=True)),
                "url": href,
                "search_snippet": clean_space(card.get_text(" ", strip=True) if card else "")[:1200],
            })
            if len(rows) >= limit:
                break

        updates: list[dict] = []
        for row in rows:
            try:
                article = context.new_page()
                article_response = _goto(article, row["url"], timeout_ms)
                doc = extract_document(article.content(), article.url, max_chars=max_chars_per_page)
                doc.update({
                    "search_title": row["title"],
                    "search_url": row["url"],
                    "search_snippet": row["search_snippet"],
                    "final_url": article.url,
                    "status": article_response.status if article_response else None,
                    "blocked_or_challenge": looks_like_challenge(doc.get("title", ""), doc.get("text", "")),
                })
                updates.append(doc)
                article.close()
            except Exception as exc:
                updates.append({**row, "error": f"{type(exc).__name__}: {exc}"})
        return {
            "query": query,
            "browser": browser_name,
            "search_status": response.status if response else None,
            "search_blocked_or_challenge": looks_like_challenge(page.title(), soup.get_text(" ", strip=True)),
            "count": len(updates),
            "updates": updates,
        }
