from __future__ import annotations

import json
import sys
import traceback

from .browser import browser_status_info
from .ops import crawl_site, download_direct, download_from_page, latest_updates, links_page, open_page, search_bing


def dispatch(action: str, payload: dict) -> dict:
    if action == "browser_status":
        return browser_status_info()
    if action == "web_open":
        return open_page(**payload)
    if action == "web_links":
        return links_page(**payload)
    if action == "web_crawl":
        return crawl_site(**payload)
    if action == "web_download_links":
        return download_from_page(**payload)
    if action == "web_download":
        return download_direct(**payload)
    if action == "web_search":
        return search_bing(**payload)
    if action == "web_latest":
        return latest_updates(**payload)
    raise ValueError(f"unknown web action: {action}")


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "worker requires one action"}))
        return 2
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        result = dispatch(sys.argv[1], payload)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
