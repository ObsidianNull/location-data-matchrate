"""
Pure matching logic: join-match classification, precision-tier assignment,
and county/borough normalization. No I/O — everything here operates on data
already in memory, so it's cheap to test exhaustively.
"""

import pandas as pd

BOROUGH_MAP = {
    "K": "BROOKLYN",
    "BK": "BROOKLYN",
    "NY": "MANHATTAN",
    "MN": "MANHATTAN",
    "QN": "QUEENS",
    "BX": "BRONX",
    "BRONX": "BRONX",
    "R": "STATEN ISLAND",
}

MATCH_UNMATCHED = "unmatched"
MATCH_NO_ADDRESS = "matched_no_address"
MATCH_STREET_ONLY = "matched_street_only"
MATCH_HOUSE_LEVEL = "matched_house_level"

TIER_HOUSE_NUMBER = "house_number"
TIER_STREET_CODE = "street_code"
TIER_INTERSECTING_STREET = "intersecting_street"
TIER_PRECINCT = "precinct"
TIER_COUNTY = "county"
TIER_NONE = "none"


def _is_blank(value) -> bool:
    """Treat None, NaN, and whitespace-only strings as equally blank —
    decided once here so the rest of the pipeline can't drift on it.
    """
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def normalize_county(value) -> str | None:
    """Map inconsistent county/borough codes to one canonical borough name.

    K/BK -> BROOKLYN, NY/MN -> MANHATTAN, QN -> QUEENS, BX/BRONX -> BRONX,
    R -> STATEN ISLAND. Anything else is upper-cased and passed through
    rather than dropped, since Source A/B may contain values outside this
    known set that are still worth comparing as-is.
    """
    if _is_blank(value):
        return None
    key = str(value).strip().upper()
    return BOROUGH_MAP.get(key, key)


def classify_match(matched: bool, house_number, street_name) -> str:
    """Match classification per spec 3.2."""
    if not matched:
        return MATCH_UNMATCHED

    house_present = not _is_blank(house_number)
    street_present = not _is_blank(street_name)

    if not house_present and not street_present:
        return MATCH_NO_ADDRESS
    if street_present and not house_present:
        return MATCH_STREET_ONLY
    return MATCH_HOUSE_LEVEL


def assign_precision_tier(
    house_number,
    street_code1,
    street_code2,
    street_code3,
    intersecting_street,
    precinct,
    county,
) -> str:
    """Precision tier per spec 3.3, most to least granular. Falls back to
    Source A's own precinct/county even for unmatched tickets — this is a
    ticket-location precision measure, not strictly a match-quality one.
    """
    if not _is_blank(house_number):
        return TIER_HOUSE_NUMBER
    if not _is_blank(street_code1) or not _is_blank(street_code2) or not _is_blank(street_code3):
        return TIER_STREET_CODE
    if not _is_blank(intersecting_street):
        return TIER_INTERSECTING_STREET
    if not _is_blank(precinct):
        return TIER_PRECINCT
    if not _is_blank(county):
        return TIER_COUNTY
    return TIER_NONE


def add_match_columns(merged_df: pd.DataFrame, matched_summons_numbers: set) -> pd.DataFrame:
    """Attach match_status and precision_tier columns to a merged Source A
    + Source B DataFrame (the output of sampling.merge_source_a_and_b).

    `matched_summons_numbers` should be the set of summons numbers that
    actually came back from Source B, computed before the merge — match
    status is decided by that membership test, not by whether any single
    Source B field happens to be non-null, because a genuinely matched
    ticket can have every address field blank (matched_no_address).
    """
    df = merged_df.copy()
    is_matched = df["summons_number"].isin(matched_summons_numbers)

    df["match_status"] = [
        classify_match(matched, house_number, street_name)
        for matched, house_number, street_name in zip(
            is_matched, df["house_number"], df["street_name"]
        )
    ]

    df["precision_tier"] = [
        assign_precision_tier(
            house_number, street_code1, street_code2, street_code3,
            intersecting_street, precinct, county,
        )
        for house_number, street_code1, street_code2, street_code3, intersecting_street, precinct, county in zip(
            df["house_number"],
            df["street_code1"],
            df["street_code2"],
            df["street_code3"],
            df["intersecting_street"],
            df["precinct"],
            df["county"],
        )
    ]

    df["county_normalized"] = df["county"].apply(normalize_county)
    df["violation_county_normalized"] = df["violation_county"].apply(normalize_county)

    return df
