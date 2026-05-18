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

The OSS surface emits values in DOLLARS — the SEC encodes 13F values
as multiples of $1000 in the XML, but this is a frequent foot-gun for
downstream callers. We multiply by 1000 once here and surface the
canonical USD figure on the Holding dataclass.
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
        value_usd                   Market value in DOLLARS (already × 1000
                                    from the SEC's-thousands encoding — note
                                    this is normalized for caller convenience).
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
    ns = ""
    if "{" in root.tag:
        ns = root.tag.split("}")[0] + "}"

    holdings: List[Holding] = []
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

        # Value: SEC encodes in $thousands. Normalize to dollars so
        # callers don't have to remember the factor.
        value_thousands = _safe_int(entry.findtext(f"{ns}value", ""))
        value_usd = value_thousands * 1000 if value_thousands is not None else None

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

        holdings.append(
            Holding(
                cusip=cusip,
                issuer_name=issuer_name,
                title_of_class=title_of_class,
                value_usd=value_usd,
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

    return holdings


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
