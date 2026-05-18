"""Smoke tests for parse_13f_infotable."""

from oxfordledge_edgar import Holding, parse_13f_infotable


_BASIC_13F_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>915560</value>
    <shrsOrPrnAmt>
      <sshPrnamt>4034214</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority>
      <Sole>4034214</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
</informationTable>"""


def test_parses_basic_13f_holding():
    holdings = parse_13f_infotable(_BASIC_13F_XML)
    assert len(holdings) == 1
    h = holdings[0]
    assert isinstance(h, Holding)
    assert h.cusip == "037833100"
    assert h.issuer_name == "APPLE INC"
    assert h.title_of_class == "COM"
    # SEC encodes value in $thousands; package normalizes to dollars.
    # 915560 * 1000 = $915,560,000.
    assert h.value_usd == 915_560_000
    assert h.shares_or_units == 4_034_214
    assert h.sh_or_prn == "SH"
    assert h.investment_discretion == "SOLE"
    assert h.voting_authority_sole == 4_034_214
    assert h.voting_authority_shared == 0
    assert h.voting_authority_none == 0


def test_invalid_cusip_skipped():
    """Rows with CUSIP 'NA' / blank / wrong-length are silently skipped."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>VALID</nameOfIssuer>
    <cusip>037833100</cusip>
    <value>1000</value>
    <shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>INVALID</nameOfIssuer>
    <cusip>NA</cusip>
    <value>500</value>
    <shrsOrPrnAmt><sshPrnamt>50</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>BLANK CUSIP</nameOfIssuer>
    <cusip></cusip>
    <value>200</value>
    <shrsOrPrnAmt><sshPrnamt>20</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
</informationTable>"""
    holdings = parse_13f_infotable(xml)
    assert len(holdings) == 1
    assert holdings[0].issuer_name == "VALID"


def test_put_call_classification():
    """Put / Call holdings carry the put_or_call discriminator."""
    xml = _BASIC_13F_XML.replace(
        "<investmentDiscretion>SOLE</investmentDiscretion>",
        "<putCall>Put</putCall><investmentDiscretion>SOLE</investmentDiscretion>",
    )
    holdings = parse_13f_infotable(xml)
    assert holdings[0].put_or_call == "Put"


def test_malformed_xml_returns_empty_list():
    assert parse_13f_infotable("") == []
    assert parse_13f_infotable("not xml") == []


def test_no_infotables_returns_empty_list():
    """Empty informationTable XML returns empty list (not raise)."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
</informationTable>"""
    assert parse_13f_infotable(xml) == []


def test_sh_or_prn_uppercased():
    """sshPrnamtType is normalized to upper-case."""
    xml = _BASIC_13F_XML.replace(
        "<sshPrnamtType>SH</sshPrnamtType>",
        "<sshPrnamtType>sh</sshPrnamtType>",
    )
    holdings = parse_13f_infotable(xml)
    assert holdings[0].sh_or_prn == "SH"
