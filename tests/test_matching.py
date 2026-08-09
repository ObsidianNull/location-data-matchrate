import numpy as np
import pandas as pd
import pytest

from src.matching import (
    MATCH_HOUSE_LEVEL,
    MATCH_NO_ADDRESS,
    MATCH_STREET_ONLY,
    MATCH_UNMATCHED,
    TIER_COUNTY,
    TIER_HOUSE_NUMBER,
    TIER_INTERSECTING_STREET,
    TIER_NONE,
    TIER_PRECINCT,
    TIER_STREET_CODE,
    add_match_columns,
    assign_precision_tier,
    classify_match,
    normalize_county,
)


# ---------------------------------------------------------------------------
# classify_match
# ---------------------------------------------------------------------------


def test_classify_match_unmatched():
    assert classify_match(False, "123", "MAIN ST") == MATCH_UNMATCHED


def test_classify_match_no_address_both_none():
    assert classify_match(True, None, None) == MATCH_NO_ADDRESS


def test_classify_match_no_address_both_empty_string():
    assert classify_match(True, "", "") == MATCH_NO_ADDRESS


def test_classify_match_no_address_treats_none_and_empty_string_identically():
    assert classify_match(True, None, "") == MATCH_NO_ADDRESS
    assert classify_match(True, "", None) == MATCH_NO_ADDRESS


def test_classify_match_no_address_whitespace_only():
    assert classify_match(True, "   ", "\t") == MATCH_NO_ADDRESS


def test_classify_match_street_only():
    assert classify_match(True, None, "MAIN ST") == MATCH_STREET_ONLY


def test_classify_match_house_level():
    assert classify_match(True, "123", "MAIN ST") == MATCH_HOUSE_LEVEL


def test_classify_match_house_present_but_street_blank_still_house_level():
    # Per spec, "matched, both present" -> house_level; a house_number with
    # no street name is an odd but real edge case, not street-only.
    assert classify_match(True, "123", None) == MATCH_HOUSE_LEVEL


def test_classify_match_ignores_house_and_street_when_unmatched():
    assert classify_match(False, "123", "MAIN ST") == MATCH_UNMATCHED


# ---------------------------------------------------------------------------
# assign_precision_tier
# ---------------------------------------------------------------------------


def test_tier_house_number_wins_when_present():
    assert assign_precision_tier("123", "1", "2", "3", "CROSS ST", "019", "NY") == TIER_HOUSE_NUMBER


def test_tier_street_code_when_no_house_number():
    assert assign_precision_tier(None, "12345", None, None, "CROSS ST", "019", "NY") == TIER_STREET_CODE


def test_tier_street_code_checks_all_three_code_fields():
    assert assign_precision_tier(None, None, None, "99999", None, "019", "NY") == TIER_STREET_CODE


def test_tier_intersecting_street_when_no_house_or_code():
    assert assign_precision_tier(None, None, None, None, "CROSS ST", "019", "NY") == TIER_INTERSECTING_STREET


def test_tier_street_code_of_zero_treated_as_unset_not_a_real_code():
    # "0" is Source B's placeholder for "no street code assigned," not a
    # real NYC street code (confirmed against live data) — must fall
    # through to intersecting_street, not be credited as street_code.
    assert (
        assign_precision_tier(None, "0", "0", "0", "CROSS ST", "019", "NY")
        == TIER_INTERSECTING_STREET
    )


def test_tier_street_code_of_zero_falls_through_to_precinct_when_no_intersecting_street():
    assert assign_precision_tier(None, "0", "0", "0", None, "019", "NY") == TIER_PRECINCT


def test_tier_precinct_fallback():
    assert assign_precision_tier(None, None, None, None, None, "019", "NY") == TIER_PRECINCT


def test_tier_county_fallback():
    assert assign_precision_tier(None, None, None, None, None, None, "NY") == TIER_COUNTY


def test_tier_none_when_everything_blank():
    assert assign_precision_tier(None, None, None, None, None, None, None) == TIER_NONE


def test_tier_treats_empty_string_same_as_none():
    assert assign_precision_tier("", "", "", "", "", "", "") == TIER_NONE


# ---------------------------------------------------------------------------
# normalize_county
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("K", "BROOKLYN"),
        ("BK", "BROOKLYN"),
        ("Kings", "BROOKLYN"),
        ("BROOK", "BROOKLYN"),
        ("NY", "MANHATTAN"),
        ("MN", "MANHATTAN"),
        ("Manha", "MANHATTAN"),
        ("Q", "QUEENS"),
        ("QN", "QUEENS"),
        ("Qns", "QUEENS"),
        ("QUEEN", "QUEENS"),
        ("BX", "BRONX"),
        ("Bronx", "BRONX"),
        ("bronx", "BRONX"),
        ("R", "STATEN ISLAND"),
        ("ST", "STATEN ISLAND"),
        ("Rich", "STATEN ISLAND"),
        ("  bk  ", "BROOKLYN"),
    ],
)
def test_normalize_county_known_values(raw, expected):
    assert normalize_county(raw) == expected


def test_normalize_county_none_returns_none():
    assert normalize_county(None) is None


def test_normalize_county_empty_string_returns_none():
    assert normalize_county("") is None


def test_normalize_county_unknown_value_passed_through_uppercased():
    assert normalize_county("weird") == "WEIRD"


# ---------------------------------------------------------------------------
# add_match_columns
# ---------------------------------------------------------------------------


def _merged_row(summons_number, **overrides):
    row = {
        "summons_number": summons_number,
        "house_number": None,
        "street_name": None,
        "street_code1": None,
        "street_code2": None,
        "street_code3": None,
        "intersecting_street": None,
        "precinct": "019",
        "county": "NY",
        "violation_county": None,
    }
    row.update(overrides)
    return row


def test_add_match_columns_end_to_end():
    merged_df = pd.DataFrame(
        [
            _merged_row("1", house_number="123", street_name="MAIN ST", violation_county="MN"),
            _merged_row("2", street_name="MAIN ST"),
            _merged_row("3"),  # not in matched set -> unmatched
        ]
    )
    matched_summons_numbers = {"1", "2"}

    result = add_match_columns(merged_df, matched_summons_numbers)

    assert result.set_index("summons_number")["match_status"].to_dict() == {
        "1": MATCH_HOUSE_LEVEL,
        "2": MATCH_STREET_ONLY,
        "3": MATCH_UNMATCHED,
    }
    assert result.set_index("summons_number")["precision_tier"].to_dict() == {
        "1": TIER_HOUSE_NUMBER,
        "2": TIER_PRECINCT,  # no house/code/intersecting -> falls back to Source A precinct
        "3": TIER_PRECINCT,
    }
    assert result.set_index("summons_number")["county_normalized"].to_dict() == {
        "1": "MANHATTAN",
        "2": "MANHATTAN",
        "3": "MANHATTAN",
    }
    assert result.loc[result["summons_number"] == "1", "violation_county_normalized"].iloc[0] == "MANHATTAN"


def test_add_match_columns_treats_nan_from_left_join_as_blank():
    # Simulates what an unmatched left-join row actually looks like: NaN,
    # not None, for every Source B-only column.
    merged_df = pd.DataFrame(
        [
            {
                "summons_number": "1",
                "house_number": np.nan,
                "street_name": np.nan,
                "street_code1": np.nan,
                "street_code2": np.nan,
                "street_code3": np.nan,
                "intersecting_street": np.nan,
                "precinct": "019",
                "county": "NY",
                "violation_county": np.nan,
            }
        ]
    )

    result = add_match_columns(merged_df, matched_summons_numbers=set())

    assert result.loc[0, "match_status"] == MATCH_UNMATCHED
    assert result.loc[0, "precision_tier"] == TIER_PRECINCT
    assert result.loc[0, "violation_county_normalized"] is None
