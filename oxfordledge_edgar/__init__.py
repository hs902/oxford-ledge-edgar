"""oxfordledge-edgar — SEC EDGAR Form 4 + 13F parsers.

Battle-tested in production at oxfordledge.com (238K+ Form 4
transactions parsed across 2,963 tickers, 0 failures).

Public API:

    parse_form4(xml_text) -> list[Form4Transaction]
    parse_13f_infotable(xml_text) -> list[Holding]

    EdgarFetcher(user_agent=..., qps=8) -> .get_text(url)

    Form4Transaction (frozen dataclass)
    Holding         (frozen dataclass)

License: MIT — see LICENSE in the repository root.
Source: https://github.com/hs902/oxford-ledge-edgar
Powered by Oxford Ledge — https://www.oxfordledge.com
"""
from __future__ import annotations

from oxfordledge_edgar._http import EdgarFetcher, RateLimiter
from oxfordledge_edgar.form4 import Form4Transaction, parse_form4
from oxfordledge_edgar.thirteenf import Holding, parse_13f_infotable

__version__ = "0.2.0"
__all__ = [
    "EdgarFetcher",
    "Form4Transaction",
    "Holding",
    "RateLimiter",
    "parse_13f_infotable",
    "parse_form4",
    "__version__",
]
