# oxfordledge-edgar

**Battle-tested SEC EDGAR Form 4 + 13F parsers, MIT-licensed.**

Extracted from the production parser at [oxfordledge.com](https://www.oxfordledge.com) — 238,825 Form 4 insider transactions parsed across 2,963 tickers with 0 failures, 115K+ filings indexed since 2026-04.

[![PyPI](https://img.shields.io/pypi/v/oxfordledge-edgar)](https://pypi.org/project/oxfordledge-edgar/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Why this exists

SEC EDGAR Form 4 (insider transactions) and 13F (institutional holdings) parsers
are everywhere — most of them are buggy, abandoned, or quietly drop edge cases.
The Oxford Ledge production parser has been running at scale for months with a
hand-corrected fixture corpus covering:

- Open-market buys/sells (codes P / S)
- Stock awards (A) — often missing `transactionPricePerShare`
- Tax-withholding events (F) — easy to mis-classify as sells
- Gifts (G) — same edge case as awards
- Indirect ownership (`directOrIndirectOwnership = "I"`) with footnote refs
- Multi-line filings (one filer × multiple transactions)
- Derivative + non-derivative tables on the same filing
- Officer + director + 10-percent-owner combinatorics
- 4 historical bad-date corrections; 44 impossible-price nullifications

This package is that parser, extracted with the bare minimum vendored
dependencies (stdlib only — no `requests`, no `httpx`, no `lxml`).

---

## Install

```sh
pip install oxfordledge-edgar
```

Python 3.10+. No runtime dependencies.

---

## Usage

### Form 4 — parse a single filing

```python
from oxfordledge_edgar import EdgarFetcher, parse_form4

fetcher = EdgarFetcher(user_agent="YourCompany research@yourcompany.com")
xml = fetcher.get_text("https://www.sec.gov/Archives/edgar/data/320193/000032019325000045/wf-form4_173456.xml")

transactions = parse_form4(xml)
for t in transactions:
    print(t.insider_name, t.transaction_code, t.shares, t.price_per_share)
```

Each `Form4Transaction` is a frozen dataclass:

```python
@dataclass(frozen=True)
class Form4Transaction:
    accession: Optional[str]
    issuer_cik: Optional[str]
    insider_name: Optional[str]
    insider_title: Optional[str]
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    transaction_date: Optional[date]
    transaction_code: Optional[str]   # P, S, A, F, G, M, etc.
    shares: Optional[float]
    price_per_share: Optional[float]
    transaction_value_usd: Optional[float]
    shares_owned_after: Optional[float]
    direct_or_indirect: Optional[str]  # "D" | "I"
    is_open_market: bool               # True iff code in {"P", "S"}
```

### 13F — parse an institutional holdings table

```python
from oxfordledge_edgar import parse_13f_infotable

xml = fetcher.get_text("https://www.sec.gov/Archives/edgar/data/.../infotable.xml")
holdings = parse_13f_infotable(xml)
for h in holdings:
    print(h.issuer_name, h.cusip, h.shares_or_units, h.value_usd)
```

`Holding` dataclass:

```python
@dataclass(frozen=True)
class Holding:
    cusip: Optional[str]
    issuer_name: Optional[str]
    title_of_class: Optional[str]
    value_usd: Optional[int]            # already in dollars (not thousands)
    shares_or_units: Optional[int]
    sh_or_prn: Optional[str]            # "SH" (shares) | "PRN" (principal)
    put_or_call: Optional[str]          # for derivatives
    investment_discretion: Optional[str]
    voting_authority_sole: Optional[int]
    voting_authority_shared: Optional[int]
    voting_authority_none: Optional[int]
```

### Rate limiting (respect SEC's 10-qps cap)

```python
from oxfordledge_edgar import EdgarFetcher

# Default: 8 qps (under SEC's published 10 qps cap, leaves headroom).
# Adjust if you have a documented arrangement with the SEC for higher.
fetcher = EdgarFetcher(
    user_agent="YourCompany research@yourcompany.com",
    qps=8,
)
```

---

## What this package does NOT do

- **Does not maintain a CIK database.** Pass URLs / accession numbers; CIK
  lookup is your caller's responsibility (use SEC's
  `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany` or vendor a
  cached CIK→ticker map).
- **Does not store anything.** `parse_form4(xml)` returns dataclasses.
  Persistence is your caller's responsibility.
- **Does not handle SEC outages or 429s with retries.** The
  `EdgarFetcher` raises a clean `urllib.error.HTTPError`; your caller
  decides retry policy.
- **Does not parse 8-K, 10-K, 10-Q, SC 13D/G, NPORT-P,** or other filing
  types. Form 4 + 13F only in v0.x. Future major versions may add more
  filing types under the same package.

---

## Schema versions targeted

- **Form 4**: SEC's 2018+ XML schema (`<ownershipDocument>` root). Earlier
  filings used a different format; this parser does not handle them.
- **13F**: SEC's `informationtable` XML schema (post-2010 13F-HR filings).

---

## Compliance notes

SEC EDGAR access requires a proper `User-Agent` header per their
[Fair Access policy](https://www.sec.gov/os/accessing-edgar-data).
The example above shows the recommended format:
`YourCompany research@yourcompany.com`. Failing to provide a real
contact will get your IP blocked.

---

## Contributing

Issues and PRs welcome. Please include a fixture XML file + expected
parsed output for any edge case you're reporting.

---

## License + attribution

MIT License — see [LICENSE](LICENSE).

This package is **maintained by [Oxford Ledge](https://www.oxfordledge.com)**,
the independent equity research platform for self-directed investors.
If you build something useful with this, a "Powered by Oxford Ledge" link
in your README or about page is appreciated but not required.

## Disclaimer

This software is provided "as is", without warranty of any kind, under
the MIT License (see LICENSE). It is a developer tool for accessing and
parsing financial data. **It is not investment, financial, legal, or
tax advice, and nothing it returns is a recommendation to buy, sell, or
hold any security.** Data may be incomplete, delayed, or inaccurate.
You are responsible for independently verifying anything you rely on
and for your own investment decisions.

## Contributions

By submitting a pull request or patch, you agree your contribution is
licensed under the same MIT License as this project (inbound = outbound).
