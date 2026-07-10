"""13F (institutional holdings) infotable parser.

Carved out of the production Oxford Ledge parser at
data/edgar.py:_parse_13f_infotable.

Public API:

    parse_13f_infotable(xml_text: str) -> list[Holding]
    Holding (frozen dataclass)

The parser takes already-fetched XML; HTTP fetching is the caller's
job. Note that 13F-HR filings have multiple files; the relevant one
is the `*infotable*.xml` document (the index.json `directory.item`
entry whose `name` ends with `.xml` and contains `infotable`).

The OSS surface emits values in DOLLARS, but the raw `<value>` unit is
a real-world foot-gun: the SEC's pre-2023 spec encoded values in
$thousands, the post-2023 spec says whole dollars — and in practice
the unit still VARIES BY FILER (many filers kept reporting thousands
after the spec change). A blanket assumption in either direction
mis-scales a large share of filings by 1000x.

This parser therefore detects the unit PER DOCUMENT: it computes the
median implied share price (value / shares) across the document's
common-stock rows and rescales only when that median falls outside
the plausible-equity band (below $1/share means the values were in
thousands; above $1,000,000/share is impossible for real equity —
Berkshire's A shares trade around $700k). Documents with too few
common-stock rows to trust a median are left in whole dollars (the
current-spec default). See `_detect_value_unit` for the exact rule.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Holding:
    """One row from a 13F infotable.

    Fields:
        cusip                       9-char CUSIP identifier (validated:
                                    first 8 alphanumeric, 9th any digit).
        issuer_name                 Name of the security issuer (raw from
                                    `<nameOfIssuer>`; may have trailing
                                    whitespace per SEC encoding).
        title_of_class              Security class (e.g. "COM", "PUT", "CALL",
                                    "PFD" for preferred). For options the
                                    title differs from the underlying common.
        value_usd                   Market value in DOLLARS. The raw
                                    `<value>` unit varies by filer
                                    (pre-2023 spec: thousands; post-2023
                                    spec: dollars; real filings: both).
                                    Normalized per document via the
                                    median-implied-price detection
                                    described in the module docstring.
        shares_or_units             `<sshPrnamt>` integer.
        sh_or_prn                   "SH" (shares) or "PRN" (principal amount,
                                    bonds). Almost always "SH" for equity
                                    13F-HR; PRN appears for fixed-income.
        put_or_call                 "Put" / "Call" / None. Set for option
                                    holdings; the underlying CUSIP appears
                                    on a separate row.
        investment_discretion       "SOLE", "DEFINED", or "OTHR". The filer's
                                    legal discretion over the position.
        other_manager               Optional reference to other-manager
                                    column when `investment_discretion`
                                    is "DEFINED".
        voting_authority_sole       Shares the filer can vote alone.
        voting_authority_shared     Shares with shared voting authority.
        voting_authority_none       Shares the filer can NOT vote.
    """

    cusip: Optional[str] = None
    issuer_name: Optional[str] = None
    title_of_class: Optional[str] = None
    value_usd: Optional[int] = None
    shares_or_units: Optional[int] = None
    sh_or_prn: Optional[str] = None
    put_or_call: Optional[str] = None
    investment_discretion: Optional[str] = None
    other_manager: Optional[str] = None
    voting_authority_sole: Optional[int] = None
    voting_authority_shared: Optional[int] = None
    voting_authority_none: Optional[int] = None


def parse_13f_infotable(xml_text: str) -> List[Holding]:
    """Parse a 13F infotable XML document into a list of Holdings.

    Returns an empty list on:
      - malformed XML (ET.ParseError)
      - infotable with no `<infoTable>` rows
      - rows with invalid CUSIPs (skipped, not failed)

    Args:
        xml_text: Already-fetched 13F infotable XML string. Fetch
                  the URL pointed at by the filing's `index.json`
                  `directory.item` entry whose `name` ends with
                  `.xml` and contains `infotable`.

    Returns:
        List of Holding. One row per `<infoTable>` node in the
        document (after CUSIP validation filtering).
    """
    if not xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        _logger.warning("13F infotable parse error: %s", e)
        return []

    # 13F infotable XML uses a default namespace
    # (e.g. http://www.sec.gov/edgar/document/thirteenf/informationtable);
    # discover it from the root tag rather than hard-coding so future
    # schema bumps don't break the parser.
    # audit-carveout: SEC XBRL namespace URI is a stable identifier, not a fetchable URL (http:// is correct per W3C XML namespace convention). External-Link Audit 2026-05-17 F8.
    ns = ""
    if "{" in root.tag:
        ns = root.tag.split("}")[0] + "}"

    # Pass 1 — extract raw rows. Values stay in the filer's raw unit
    # until the whole document has been read: the unit is a per-document
    # property (see module docstring) and can only be decided from the
    # full row population.
    rows: List[dict] = []
    for entry in root.findall(f".//{ns}infoTable"):
        cusip = (entry.findtext(f"{ns}cusip", "") or "").strip().upper()
        if not _validate_cusip(cusip):
            # Common: 13F historically allowed CUSIP "NA" / "N/A" /
            # blank for foreign securities; skip these rows but don't
            # fail the whole parse.
            continue

        issuer_name = (entry.findtext(f"{ns}nameOfIssuer", "") or "").strip() or None
        title_of_class = (entry.findtext(f"{ns}titleOfClass", "") or "").strip() or None
        put_or_call = (entry.findtext(f"{ns}putCall", "") or "").strip() or None

        value_raw = _safe_int(entry.findtext(f"{ns}value", ""))

        # Share / principal-amount block
        shares_block = entry.find(f"{ns}shrsOrPrnAmt")
        shares_amt: Optional[int] = None
        sh_or_prn: Optional[str] = None
        if shares_block is not None:
            shares_amt = _safe_int(shares_block.findtext(f"{ns}sshPrnamt", ""))
            sh_or_prn = (shares_block.findtext(f"{ns}sshPrnamtType", "") or "").strip().upper() or None

        # Investment discretion + other-manager reference
        invest_disc = (entry.findtext(f"{ns}investmentDiscretion", "") or "").strip().upper() or None
        other_mgr = (entry.findtext(f"{ns}otherManager", "") or "").strip() or None

        # Voting authority block
        voting_block = entry.find(f"{ns}votingAuthority")
        v_sole = v_shared = v_none = None
        if voting_block is not None:
            v_sole = _safe_int(voting_block.findtext(f"{ns}Sole", ""))
            v_shared = _safe_int(voting_block.findtext(f"{ns}Shared", ""))
            v_none = _safe_int(voting_block.findtext(f"{ns}None", ""))

        rows.append(
            dict(
                cusip=cusip,
                issuer_name=issuer_name,
                title_of_class=title_of_class,
                value_raw=value_raw,
                shares_or_units=shares_amt,
                sh_or_prn=sh_or_prn,
                put_or_call=put_or_call,
                investment_discretion=invest_disc,
                other_manager=other_mgr,
                voting_authority_sole=v_sole,
                voting_authority_shared=v_shared,
                voting_authority_none=v_none,
            )
        )

    # Pass 2 — decide the document's value unit, then build the frozen rows.
    factor = _detect_value_unit(rows)

    holdings: List[Holding] = []
    for row in rows:
        value_raw = row.pop("value_raw")
        if value_raw is None:
            value_usd = None
        elif factor == _FACTOR_THOUSANDS:
            value_usd = value_raw * 1000
        elif factor == _FACTOR_OVERSCALED:
            value_usd = value_raw // 1000
        else:
            value_usd = value_raw
        holdings.append(Holding(value_usd=value_usd, **row))

    return holdings


# ── Per-document value-unit detection ────────────────────────────────
#
# The `<value>` unit varies by filer (pre-2023 spec: $thousands;
# post-2023 spec: dollars; real filings: both). Decide per document
# from the median implied share price (value / shares) across
# common-stock rows: below $1/share the values must be in thousands;
# above $1,000,000/share is impossible for real equity (Berkshire's
# A shares, the most expensive listed US stock, trade around $700k).
# The median across a diversified document is robust to per-row
# outliers (a lone penny stock or BRK.A row can't move it), and the
# minimum-row gate keeps a tiny or penny-only document from tripping
# a false correction — those stay in whole dollars, the current-spec
# default.
_UNIT_CHECK_MIN_COMMON_ROWS = 5
_IMPLIED_PX_LOW_FLOOR = 1.0          # median below this => values were in $thousands
_IMPLIED_PX_HIGH_CEIL = 1_000_000.0  # median above this => values were over-scaled
_FACTOR_DOLLARS = "dollars"          # leave as-is
_FACTOR_THOUSANDS = "thousands"      # multiply by 1000
_FACTOR_OVERSCALED = "overscaled"    # divide by 1000


def _detect_value_unit(rows: List[dict]) -> str:
    """Classify a document's `<value>` unit from its common-stock rows.

    Returns one of _FACTOR_DOLLARS / _FACTOR_THOUSANDS /
    _FACTOR_OVERSCALED. Only whole-document unit mistakes are
    correctable this way; per-row anomalies (option notionals, bond
    principal rows) deliberately do NOT move the median and are the
    caller's concern.
    """
    prices = [
        row["value_raw"] / row["shares_or_units"]
        for row in rows
        if row.get("sh_or_prn") == "SH"
        and not row.get("put_or_call")
        and (row.get("title_of_class") or "").upper().startswith("COM")
        and (row.get("shares_or_units") or 0) > 0
        and (row.get("value_raw") or 0) > 0
    ]
    if len(prices) < _UNIT_CHECK_MIN_COMMON_ROWS:
        return _FACTOR_DOLLARS
    prices.sort()
    med = prices[len(prices) // 2]
    if med < _IMPLIED_PX_LOW_FLOOR:
        _logger.info(
            "13F value-unit: median implied $%.4f/sh < $%g — document "
            "reports in $thousands; rescaling x1000 (%d common rows)",
            med, _IMPLIED_PX_LOW_FLOOR, len(prices),
        )
        return _FACTOR_THOUSANDS
    if med > _IMPLIED_PX_HIGH_CEIL:
        _logger.info(
            "13F value-unit: median implied $%.0f/sh > $%.0f — document "
            "values over-scaled; rescaling /1000 (%d common rows)",
            med, _IMPLIED_PX_HIGH_CEIL, len(prices),
        )
        return _FACTOR_OVERSCALED
    return _FACTOR_DOLLARS


def _validate_cusip(cusip: str) -> bool:
    """CUSIP basic format check.

    A real CUSIP is 9 chars: first 8 are alphanumeric (issuer + issue),
    9th is a check digit (always a digit). The SEC's 13F filings include
    placeholder strings ("NA", "N/A", "") for some foreign or unusual
    securities; skip those rather than fail the parse.

    For this OSS package, we accept any 9-char string where the first 8
    are alphanumeric. We do NOT verify the check digit — that's a
    caller-side decision based on how strict their downstream pipeline
    is.
    """
    if not cusip or len(cusip) != 9:
        return False
    if cusip == "000000000":
        # SEC placeholder row: confidential-treatment / nothing-to-report
        # filings carry a single infoTable with nameOfIssuer=NA,
        # cusip=000000000, value=0, shares=0. Issuer number 000000 does
        # not exist; the row is filing plumbing, not a holding.
        return False
    return cusip[:8].isalnum()


def _safe_int(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


__all__ = ["Holding", "parse_13f_infotable"]
