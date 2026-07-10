"""Smoke tests for parse_13f_infotable."""

# audit-carveout: SEC XBRL/EDGAR namespace URIs (http://www.sec.gov/edgar/document/thirteenf/informationtable) embedded in the XML fixtures below are stable XML namespace identifiers, NOT fetch targets. Rewriting to https:// would no longer match the namespace declared in actual SEC 13F filings and would break the parser smoke tests. External-Link Audit 2026-05-17 F8.

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
    # Value-unit detection needs >=5 common-stock rows to trust a
    # median; a single-row document is undecidable and stays in whole
    # dollars (the post-2023-spec default). See
    # test_value_unit_thousands_detected for the rescale path.
    assert h.value_usd == 915_560
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


def test_placeholder_row_skipped():
    """Confidential-treatment / nothing-to-report filings carry a single
    placeholder row (nameOfIssuer=NA, cusip=000000000, value=0); it is
    filing plumbing, not a holding."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>NA</nameOfIssuer>
    <titleOfClass>NA</titleOfClass>
    <cusip>000000000</cusip>
    <value>0</value>
    <shrsOrPrnAmt><sshPrnamt>0</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
</informationTable>"""
    assert parse_13f_infotable(xml) == []


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


# ── Per-document value-unit detection ────────────────────────────────
#
# The raw <value> unit varies by filer (pre-2023 spec: $thousands;
# post-2023 spec: dollars). The parser decides per document from the
# median implied share price across common-stock rows.

def _doc(rows):
    """Build an informationTable XML from (value, shares) COM rows."""
    tables = "".join(
        f"""  <infoTable>
    <nameOfIssuer>ISSUER {i}</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>03783310{i}</cusip>
    <value>{value}</value>
    <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
"""
        for i, (value, shares) in enumerate(rows)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">\n'
        f"{tables}</informationTable>"
    )


def test_value_unit_thousands_detected():
    """A thousands-reporting document (median implied price << $1) is
    rescaled x1000 — the pre-2023 spec unit many filers still use."""
    # 5 rows shaped like real thousands-encoded values: e.g. $150/sh
    # stock -> value 15000 (thousands) over 100,000 shares = 0.15 implied.
    rows = [(15_000, 100_000), (9_000, 50_000), (30_000, 120_000),
            (4_500, 30_000), (22_000, 90_000)]
    holdings = parse_13f_infotable(_doc(rows))
    assert len(holdings) == 5
    assert holdings[0].value_usd == 15_000_000  # 15000 * 1000
    assert holdings[3].value_usd == 4_500_000


def test_value_unit_dollars_left_unchanged():
    """A dollars-reporting document (plausible implied prices) is NOT
    multiplied — the blanket-x1000 bug this test pins against would
    report these 1000x high."""
    rows = [(15_000_000, 100_000), (9_000_000, 50_000), (30_000_000, 120_000),
            (4_500_000, 30_000), (22_000_000, 90_000)]
    holdings = parse_13f_infotable(_doc(rows))
    assert len(holdings) == 5
    assert holdings[0].value_usd == 15_000_000
    assert holdings[4].value_usd == 22_000_000


def test_value_unit_overscaled_divided():
    """A document whose median implied price is above $1M/sh (impossible
    for real equity) is rescaled back down /1000."""
    rows = [(15_000_000_000, 1_000), (9_000_000_000, 500),
            (30_000_000_000, 1_200), (4_500_000_000, 300),
            (22_000_000_000, 900)]
    holdings = parse_13f_infotable(_doc(rows))
    assert holdings[0].value_usd == 15_000_000
    assert holdings[2].value_usd == 30_000_000


def test_value_unit_undecidable_defaults_to_dollars():
    """Fewer than 5 common-stock rows -> no trusted median -> whole
    dollars (current-spec default), even when the implied price looks
    thousands-ish."""
    rows = [(15_000, 100_000), (9_000, 50_000)]
    holdings = parse_13f_infotable(_doc(rows))
    assert holdings[0].value_usd == 15_000
    assert holdings[1].value_usd == 9_000


def test_value_unit_median_robust_to_outlier_row():
    """A lone anomalous row (penny implied price) cannot flip a
    dollars-reporting document into a false x1000 correction."""
    rows = [(15_000_000, 100_000), (9_000_000, 50_000), (30_000_000, 120_000),
            (4_500_000, 30_000), (22_000_000, 90_000),
            (5_000, 1_000_000)]  # the outlier: $0.005 implied
    holdings = parse_13f_infotable(_doc(rows))
    assert holdings[0].value_usd == 15_000_000  # unchanged
    assert holdings[5].value_usd == 5_000       # outlier row untouched too
