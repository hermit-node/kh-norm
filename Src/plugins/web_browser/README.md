# Norm Web Browser

Optional first-class Norm plugin providing bounded public-web browsing, search, rendered-page text extraction, link discovery/crawling, and file downloads.

It uses Playwright to drive a real Chromium-family browser, preferring installed Microsoft Edge, then Chrome, then a Playwright-managed Chromium. Beautiful Soup + Soup Sieve handle DOM/link selection; Trafilatura is used opportunistically for main-text/metadata extraction with a Beautiful Soup fallback.

## Exposed tools

- `browser_status()`
- `web_open(url, ...)`
- `web_links(url, selector="", same_domain=False, extensions="", ...)`
- `web_crawl(start_url, max_pages=20, max_depth=1, same_domain=True, ...)`
- `web_download_links(page_url, extensions="pdf,zip", destination="", ...)`
- `web_download(url, destination="", filename="", ...)`
- `web_search(query, max_results=10, news=False, ...)`
- `web_latest(query, max_results=5, ...)`

Browser sessions persist under `Documents\Norm\workspace\web\sessions`. Downloads default to `Documents\Norm\workspace\web\downloads`. Explicit download destinations are confined to the Norm workspace.

The plugin intentionally does not bypass CAPTCHAs, anti-bot challenges, paywalls, or access controls. It rejects localhost, loopback, link-local, and private-network destinations so untrusted pages cannot turn the crawler into an internal-network probe.
