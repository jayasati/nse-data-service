"""Header construction for NSE requests.

NSE blocks requests that look automated by inspecting User-Agent and Referer.
- UA: a real Chrome string passes; obvious bots (python-requests/2.x) get 403.
- Referer: must match a page that would naturally trigger this API call in a
  browser. The endpoint→referer mapping lives in config/endpoints.yaml; this
  module just builds the header dict from whatever referer the caller passes.
"""

from __future__ import annotations
import random
from typing import Mapping


NSE_ORIGIN="https://www.nseindia.com"
DEFAULT_REFERER=NSE_ORIGIN+"/"

_BASE_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# Real Chrome UAs. Keep this list small — a giant pool is itself a fingerprint.
# Bump the Chrome version when stable Chrome moves significantly (every ~3 months).

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def pick_user_agent() -> str:
    return random.choice(_USER_AGENTS)

def build_headers(
        referer:str |None=None,
        extra :Mapping[str,str]|None=None,
    )->dict[str,str]:
        h=dict(_BASE_HEADERS)
        h["User_Agent"]=pick_user_agent()
        h["Referer"]=referer or DEFAULT_REFERER
        h["Origin"]=NSE_ORIGIN

        if extra :
            h.update(extra)
        return h

