from __future__ import annotations

from ._bridge import call


def browser_status() -> dict:
    """Report Playwright availability, detected system browsers, and the web workspace location."""
    return call("browser_status", {})


def web_open(url: str, session: str = "default", headless: bool = True, timeout_ms: int = 30000, max_chars: int = 50000) -> dict:
    """Open one public web page in a real Chromium browser and return cleaned main text plus metadata."""
    return call("web_open", {"url": url, "session": session, "headless": headless, "timeout_ms": timeout_ms, "max_chars": max_chars}, timeout_seconds=max(60, timeout_ms // 1000 + 30))


def web_links(url: str, selector: str = "", same_domain: bool = False, extensions: str = "", max_links: int = 200, session: str = "default", headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Render a page, then enumerate bounded links using Beautiful Soup/Soup Sieve CSS selectors and optional extension/domain filters."""
    return call("web_links", {"url": url, "session": session, "headless": headless, "timeout_ms": timeout_ms, "selector": selector, "same_domain": same_domain, "extensions": extensions, "max_links": max_links}, timeout_seconds=max(60, timeout_ms // 1000 + 30))


def web_crawl(start_url: str, max_pages: int = 20, max_depth: int = 1, same_domain: bool = True, max_chars_per_page: int = 12000, session: str = "default", headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Breadth-first crawl a bounded public-site link graph with rendered-page text extraction; private-network targets are rejected."""
    budget = max(90, min(900, (max_pages * max(timeout_ms, 5000)) // 1000 + 30))
    return call("web_crawl", {"start_url": start_url, "session": session, "headless": headless, "timeout_ms": timeout_ms, "max_pages": max_pages, "max_depth": max_depth, "same_domain": same_domain, "max_chars_per_page": max_chars_per_page}, timeout_seconds=budget)


def web_download_links(page_url: str, extensions: str = "pdf,zip", destination: str = "", selector: str = "", max_files: int = 20, max_bytes_per_file: int = 134217728, session: str = "default", headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Render a page, find matching file links, and download them into Norm's workspace using the browser session's cookies."""
    return call("web_download_links", {"page_url": page_url, "destination": destination, "extensions": extensions, "selector": selector, "max_files": max_files, "max_bytes_per_file": max_bytes_per_file, "session": session, "headless": headless, "timeout_ms": timeout_ms}, timeout_seconds=900)


def web_search(query: str, max_results: int = 10, news: bool = False, session: str = "default", headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Search the public web or Bing News through a real browser and return bounded result titles, URLs, and snippets for follow-up extraction."""
    return call("web_search", {"query": query, "max_results": max_results, "news": news, "session": session, "headless": headless, "timeout_ms": timeout_ms}, timeout_seconds=max(60, timeout_ms // 1000 + 30))


def web_download(url: str, destination: str = "", filename: str = "", max_bytes: int = 134217728, session: str = "default", headless: bool = True) -> dict:
    """Download one public HTTP(S) URL into Norm's workspace using cookies from the named persistent browser session."""
    return call("web_download", {"url": url, "destination": destination, "filename": filename, "max_bytes": max_bytes, "session": session, "headless": headless}, timeout_seconds=900)


def web_latest(query: str, max_results: int = 5, max_chars_per_page: int = 8000, session: str = "default", headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Search Bing News, open the top bounded results in the browser, and return extracted text plus publication metadata for latest-update summaries."""
    budget = max(90, min(900, (max_results * max(timeout_ms, 5000)) // 1000 + 60))
    return call("web_latest", {"query": query, "max_results": max_results, "max_chars_per_page": max_chars_per_page, "session": session, "headless": headless, "timeout_ms": timeout_ms}, timeout_seconds=budget)
