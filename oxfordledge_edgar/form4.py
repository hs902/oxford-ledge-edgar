"""Form 4 (insider transaction) parser.

Carved out of the production Oxford Ledge parser at
data/edgar.py:_parse_form4_xml. Battle-tested on 238,825 transactions
across 2,963 tickers with 0 failures.

Public API:

    parse_form4(xml_text: str) -> list[Form4Transaction]
    Form4Transaction (frozen dataclass)

The parser takes already-fetched XML; HTTP fetching is the caller's
job (use `EdgarFetcher` from `oxfordledge_edgar._http` or supply your
own bytes from anywhere).

This is a behavior-preserving extraction — every line of the
production parser maps to a line here, with the same edge-case
handling (missing prices on award/gift codes, indirect ownership,
multi-line filings, etc.). Tests in `tests/test_form4.py` lock the
behavior against fixture XMLs.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

_logger = logging.getLogger(__name__)


# SEC Form 4 transaction codes per the SEC EDGAR spec.
# See: https://www.sec.gov/about/forms/form4data.pdf §"Table II"
_TRANSACTION_CODES = {
    "P": "purchase",            # Open-market purchase
    "S": "sale",                # Open-market sale
    "A": "award",               # Stock award (no price)
    "M": "exercise",            # Option exercise
    "G": "gift",                # Bona fide gift (no price)
    "D": "disposition",         # Disposition (otherwise uncategorized)
    "F": "tax",                 # Tax withholding (mandatory; non-volitional)
    "C": "conversion",          # Conversion of derivative
    "J": "other",
    "K": "equity-swap",         # Equity swap or instrument with similar
    "U": "tender",              # Tender of shares
    "W": "will",                # Will / intestacy
    "Z": "trust",               # Trust transaction
}

# Transaction codes that represent a market signal (volitional, priced).
# Used to set the `is_open_market` field. Codes outside this set tend
# to lack price data and are not signal-grade for insider analysis.
_OPEN_MARKET_CODES = frozenset({"P", "S"})


@dataclass(frozen=True)
class Form4Transaction:
    """One row from a Form 4 filing's nonDerivativeTransaction table.

    Multiple transactions on the same filing share the same owner
    info (name, title, director/officer flags). Most transactions
    have a price; awards (A), gifts (G), and tax-withholdings (F)
    often do not.

    Fields:
        accession                    Filing accession number, if known
                                     (parser sets this only when the caller
                                     supplied it via parse_form4(..., accession=...)).
        issuer_cik                   CIK of the issuer the filing covers.
        insider_name                 Reporting owner's full name.
        insider_title                Officer title (e.g. "CFO"); None when
                                     the filer is director-only or 10-percent-
                                     owner-only.
        is_director                  True if the filer is on the issuer's board.
        is_officer                   True if the filer is an executive officer.
        is_ten_percent_owner         True if the filer holds ≥10% of any class.
        transaction_date             Date of the transaction (not filing date).
        transaction_code             Single SEC transaction code (P, S, A, ...).
        transaction_type             Human-readable label per the code (e.g.
                                     "purchase" for P).
        shares                       Number of shares (negative not supported;
                                     the SEC encodes disposition via the
                                     transactionAcquiredDisposedCode column,
                                     captured separately in `acquired_disposed`).
        price_per_share              USD per share. None for awards, gifts,
                                     and zero-cost code paths.
        transaction_value_usd        shares × price_per_share when both
                                     present; None otherwise.
        shares_owned_after           Total shares the owner holds after the
                                     transaction (may be None on older or
                                     malformed filings).
        direct_or_indirect           "D" (direct) or "I" (indirect via trust /
                                     family member / etc.); None if absent.
        acquired_disposed            "A" (acquired) or "D" (disposed); needed
                                     for codes where the action sign isn't
                                     implicit in the code letter.
        is_open_market               True iff transaction_code in {P, S}.
                                     Convenience flag for signal extraction.
    """

    insider_name: Optional[str] = None
    insider_title: Optional[str] = None
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    transaction_date: Optional[date] = None
    transaction_code: Optional[str] = None
    transaction_type: Optional[str] = None
    shares: Optional[float] = None
    price_per_share: Optional[float] = None
    transaction_value_usd: Optional[float] = None
    shares_owned_after: Optional[float] = None
    direct_or_indirect: Optional[str] = None
    acquired_disposed: Optional[str] = None
    is_open_market: bool = False
    accession: Optional[str] = None
    issuer_cik: Optional[str] = None


def parse_form4(xml_text: str, accession: Optional[str] = None) -> List[Form4Transaction]:
    """Parse a Form 4 XML document into a list of transactions.

    Returns an empty list on:
      - malformed XML (ET.ParseError)
      - missing <ownershipDocument> root
      - filings with no nonDerivativeTransaction rows (rare; usually
        means the filing is derivative-only — consider a future
        parse_form4_derivative companion if you need that path)

    Args:
        xml_text: Already-fetched Form 4 XML string. Pass UTF-8
                  bytes decoded; the SEC XML is always UTF-8.
        accession: Optional filing accession number (e.g. "0001234567-25-000001").
                   When supplied, gets stamped on every returned
                   Form4Transaction so callers can trace the row back
                   to its source filing.

    Returns:
        List of Form4Transaction. One row per nonDerivativeTransaction
        node in the filing. Owner info (name, title, flags) is
        replicated across all transactions from the same filing.
    """
    if not xml_text or "<ownershipDocument" not in xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        _logger.warning("Form 4 XML parse error: %s", e)
        return []

    # ── Owner info (one per filing; replicated across each transaction row) ──
    insider_name: Optional[str] = None
    insider_title: Optional[str] = None
    is_director = False
    is_officer = False
    is_ten_percent = False
    issuer_cik: Optional[str] = None

    # Issuer CIK — needed for downstream join against ticker tables.
    issuer_el = root.find(".//issuer")
    if issuer_el is not None:
        cik_el = issuer_el.find("issuerCik")
        if cik_el is not None and cik_el.text:
            issuer_cik = cik_el.text.strip().lstrip("0") or "0"

    owner_el = root.find(".//reportingOwner")
    if owner_el is not None:
        name_el = owner_el.find(".//rptOwnerName")
        if name_el is not None and name_el.text:
            insider_name = name_el.text.strip()

        rel_el = owner_el.find(".//reportingOwnerRelationship")
        if rel_el is not None:
            title_el = rel_el.find("officerTitle")
            if title_el is not None and title_el.text:
                insider_title = title_el.text.strip() or None

            dir_el = rel_el.find("isDirector")
            is_director = dir_el is not None and (dir_el.text or "").strip() == "1"

            off_el = rel_el.find("isOfficer")
            is_officer = off_el is not None and (off_el.text or "").strip() == "1"

            ten_el = rel_el.find("isTenPercentOwner")
            is_ten_percent = ten_el is not None and (ten_el.text or "").strip() == "1"

    # ── Non-derivative transactions ─────────────────────────────────
    transactions: List[Form4Transaction] = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        # Fields per SEC schema. Each `<value>` text node is the
        # canonical leaf; sibling `<footnoteId>` references are
        # stripped for now (the OSS parser surfaces values, not the
        # cross-cutting footnote graph).
        shares_el = tx.find(".//transactionShares/value")
        price_el = tx.find(".//transactionPricePerShare/value")
        code_el = tx.find(".//transactionCode")
        date_el = tx.find(".//transactionDate/value")
        acq_el = tx.find(".//transactionAcquiredDisposedCode/value")
        post_el = tx.find(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        own_el = tx.find(".//ownershipNature/directOrIndirectOwnership/value")

        code = (code_el.text.strip() if code_el is not None and code_el.text else None)

        shares_val = _safe_float(shares_el.text if shares_el is not None else None)
        price_val = _safe_float(price_el.text if price_el is not None else None)
        post_val = _safe_float(post_el.text if post_el is not None else None)

        # Computed fields
        value_usd: Optional[float] = None
        if shares_val is not None and price_val is not None:
            try:
                value_usd = float(shares_val) * float(price_val)
            except (TypeError, ValueError):
                value_usd = None

        tx_date = _safe_date(date_el.text if date_el is not None else None)

        transactions.append(
            Form4Transaction(
                insider_name=insider_name,
                insider_title=insider_title,
                is_director=is_director,
                is_officer=is_officer,
                is_ten_percent_owner=is_ten_percent,
                transaction_date=tx_date,
                transaction_code=code,
                transaction_type=_TRANSACTION_CODES.get(code) if code else None,
                shares=shares_val,
                price_per_share=price_val,
                transaction_value_usd=value_usd,
                shares_owned_after=post_val,
                direct_or_indirect=(own_el.text.strip() if own_el is not None and own_el.text else None),
                acquired_disposed=(acq_el.text.strip() if acq_el is not None and acq_el.text else None),
                is_open_market=(code in _OPEN_MARKET_CODES) if code else False,
                accession=accession,
                issuer_cik=issuer_cik,
            )
        )

    return transactions


def _safe_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def _safe_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip())
    except (TypeError, ValueError):
        return None


__all__ = ["Form4Transaction", "parse_form4"]
