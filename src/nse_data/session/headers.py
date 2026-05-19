"""Header construction for NSE requests.

Two header sets, not one:
- Warm-up GETs to /  and the referer page look like a fresh browser tab
  (Sec-Fetch-Site: none, Mode: navigate, Dest: document).
- API calls to /api/* look like a same-origin XHR from a page the user
  is already on (Sec-Fetch-Site: same-origin, Mode: cors, Dest: empty).

Mixing these up gets you 403'd by Akamai — the values are cross-checked
against whether cookies look like a real browsing session.

UA is picked ONCE per SessionManager lifetime and reused. Rotating UA per
request breaks session continuity (cookies are bound to the UA that
created them) and is itself a bot fingerprint.
"""

from __future__ import annotations

from typing import Mapping


NSE_ORIGIN = "https://www.nseindia.com"
DEFAULT_REFERER = NSE_ORIGIN + "/"

# Bump when stable Chrome moves a major version. Check: navigator.userAgent
# in any Chrome DevTools console.
_CHROME_VERSION = "131"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{_CHROME_VERSION}.0.0.0 Safari/537.36"
)

_BASE_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "sec-ch-ua": (
        f'"Google Chrome";v="{_CHROME_VERSION}", '
        f'"Chromium";v="{_CHROME_VERSION}", '
        '"Not_A Brand";v="24"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def build_warmup_headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Headers for the warm-up GETs (homepage and referer page)."""
    h = dict(_BASE_HEADERS)
    h.update({
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Upgrade-Insecure-Requests": "1",
    })
    if extra:
        h.update(extra)
    return h


def build_api_headers(
    referer: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Headers for /api/* JSON calls — same-origin XHR from an NSE page."""
    h = dict(_BASE_HEADERS)
    h.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": referer or DEFAULT_REFERER,
        "Origin": NSE_ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "X-Requested-With": "XMLHttpRequest",
    })
    if extra:
        h.update(extra)
    return h


# Backwards-compat shim — your existing manager.py imports build_headers.
# Keep this until you update the import sites, then delete.
def build_headers(
    referer: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Deprecated. Use build_api_headers() or build_warmup_headers()."""
    return build_api_headers(referer, extra)