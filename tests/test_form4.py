"""Smoke tests for parse_form4. Each test pins one edge case from
the production parser's battle-tested set.
"""
from datetime import date

from oxfordledge_edgar import Form4Transaction, parse_form4


_BASIC_SALE_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>APPLE INC</issuerName>
  </issuer>
  <reportingOwner>
    <rptOwnerName>COOK TIMOTHY D</rptOwnerName>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2025-03-15</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>175.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>3300000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parses_basic_open_market_sale():
    txns = parse_form4(_BASIC_SALE_XML)
    assert len(txns) == 1
    t = txns[0]
    assert isinstance(t, Form4Transaction)
    assert t.insider_name == "COOK TIMOTHY D"
    assert t.insider_title == "Chief Executive Officer"
    assert t.is_director is True
    assert t.is_officer is True
    assert t.is_ten_percent_owner is False
    assert t.transaction_date == date(2025, 3, 15)
    assert t.transaction_code == "S"
    assert t.transaction_type == "sale"
    assert t.shares == 10000.0
    assert t.price_per_share == 175.50
    assert t.transaction_value_usd == 1755000.0
    assert t.shares_owned_after == 3300000.0
    assert t.direct_or_indirect == "D"
    assert t.acquired_disposed == "D"
    assert t.is_open_market is True
    assert t.issuer_cik == "320193"


def test_award_has_no_price_no_value():
    """Awards (code A) typically have no price; transaction_value_usd should be None."""
    xml = _BASIC_SALE_XML.replace(
        "<transactionCode>S</transactionCode>",
        "<transactionCode>A</transactionCode>",
    ).replace(
        "<transactionPricePerShare><value>175.50</value></transactionPricePerShare>",
        "<transactionPricePerShare><value></value></transactionPricePerShare>",
    )
    txns = parse_form4(xml)
    assert len(txns) == 1
    t = txns[0]
    assert t.transaction_code == "A"
    assert t.transaction_type == "award"
    assert t.price_per_share is None
    assert t.transaction_value_usd is None
    assert t.is_open_market is False


def test_indirect_ownership_flag():
    """directOrIndirectOwnership = 'I' surfaces correctly."""
    xml = _BASIC_SALE_XML.replace(
        "<directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>",
        "<directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>",
    )
    txns = parse_form4(xml)
    assert txns[0].direct_or_indirect == "I"


def test_ten_percent_owner_flag():
    xml = _BASIC_SALE_XML.replace(
        "</reportingOwnerRelationship>",
        "<isTenPercentOwner>1</isTenPercentOwner></reportingOwnerRelationship>",
    )
    txns = parse_form4(xml)
    assert txns[0].is_ten_percent_owner is True


def test_accession_stamps_through():
    txns = parse_form4(_BASIC_SALE_XML, accession="0001234567-25-000001")
    assert txns[0].accession == "0001234567-25-000001"


def test_malformed_xml_returns_empty_list():
    assert parse_form4("not xml") == []
    assert parse_form4("") == []
    assert parse_form4("<wrong-root>x</wrong-root>") == []


def test_filing_with_no_transactions_returns_empty_list():
    """Some Form 4 filings (initial statements that share Form 3 semantics)
    have no nonDerivativeTransaction nodes."""
    xml = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik></issuer>
  <reportingOwner><rptOwnerName>X</rptOwnerName></reportingOwner>
</ownershipDocument>"""
    assert parse_form4(xml) == []


def test_multi_line_filing_replicates_owner_info():
    """Multiple transactions in one filing should each carry the same
    owner info (name, title, flags)."""
    xml = _BASIC_SALE_XML.replace(
        "</nonDerivativeTable>",
        """<nonDerivativeTransaction>
            <transactionDate><value>2025-03-16</value></transactionDate>
            <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
            <transactionAmounts>
              <transactionShares><value>500</value></transactionShares>
              <transactionPricePerShare><value>177.00</value></transactionPricePerShare>
              <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
          </nonDerivativeTransaction></nonDerivativeTable>""",
    )
    txns = parse_form4(xml)
    assert len(txns) == 2
    assert txns[0].insider_name == txns[1].insider_name == "COOK TIMOTHY D"
    assert txns[0].is_officer is True
    assert txns[1].is_officer is True
    assert txns[0].transaction_code == "S"
    assert txns[1].transaction_code == "P"
    assert txns[1].is_open_market is True


def test_director_only_no_officer_title():
    """When the filer is director-only (not officer), insider_title is None."""
    xml = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik></issuer>
  <reportingOwner>
    <rptOwnerName>BOARD MEMBER</rptOwnerName>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2025-03-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>175.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""
    txns = parse_form4(xml)
    t = txns[0]
    assert t.is_director is True
    assert t.is_officer is False
    assert t.insider_title is None
