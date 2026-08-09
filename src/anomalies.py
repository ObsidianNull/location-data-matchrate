"""
Cross-field consistency checks, anomaly flags, and fiscal-year tag
monitoring.

Operates on the merged + classified DataFrame produced by
sampling.merge_source_a_and_b() and matching.add_match_columns(). Every
check here is scoped to matched tickets only — an unmatched ticket has no
Source B data to compare, so e.g. flagging "Source B issue_date is null"
for it would be noise, not a real anomaly.
"""

from datetime import date, datetime

import pandas as pd

from src.matching import MATCH_UNMATCHED, normalize_county

ANOMALY_NULL_ISSUE_DATE_B = "null_issue_date_source_b"
ANOMALY_ISSUE_DATE_MISMATCH = "issue_date_mismatch"
ANOMALY_FUTURE_ISSUE_DATE_B = "future_issue_date_source_b"
ANOMALY_PRECINCT_MISMATCH = "precinct_mismatch"
ANOMALY_COUNTY_MISMATCH = "county_mismatch"

ALL_ANOMALY_TYPES = [
    ANOMALY_NULL_ISSUE_DATE_B,
    ANOMALY_ISSUE_DATE_MISMATCH,
    ANOMALY_FUTURE_ISSUE_DATE_B,
    ANOMALY_PRECINCT_MISMATCH,
    ANOMALY_COUNTY_MISMATCH,
]


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _as_date(value):
    if _is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def normalize_precinct(value):
    """Strip whitespace and leading zeros so '019' and '19' compare equal."""
    if _is_blank(value):
        return None
    stripped = str(value).strip().lstrip("0")
    return stripped or "0"


def precinct_mismatch(precinct_a, precinct_b) -> bool:
    """True only when both sides have a value and they disagree — a blank
    on either side means we can't verify agreement, so it's not flagged.
    """
    norm_a = normalize_precinct(precinct_a)
    norm_b = normalize_precinct(precinct_b)
    if norm_a is None or norm_b is None:
        return False
    return norm_a != norm_b


def county_mismatch(county_a, county_b) -> bool:
    norm_a = normalize_county(county_a)
    norm_b = normalize_county(county_b)
    if norm_a is None or norm_b is None:
        return False
    return norm_a != norm_b


def issue_date_mismatch(issue_date_a, issue_date_b) -> bool:
    date_a = _as_date(issue_date_a)
    date_b = _as_date(issue_date_b)
    if date_a is None or date_b is None:
        return False
    return date_a != date_b


def is_null_issue_date_b(issue_date_b) -> bool:
    return _is_blank(issue_date_b)


def is_future_issue_date_b(issue_date_b, today: date) -> bool:
    date_b = _as_date(issue_date_b)
    if date_b is None:
        return False
    return date_b > today


def _row_anomaly_flags(row, today: date) -> dict:
    matched = row["match_status"] != MATCH_UNMATCHED
    issue_date_b = row["issue_date_source_b"]

    null_issue_date_b = matched and is_null_issue_date_b(issue_date_b)
    future_issue_date_b = matched and is_future_issue_date_b(issue_date_b, today)
    date_mismatch = matched and issue_date_mismatch(row["issue_date"], issue_date_b)
    precinct_mismatch_flag = matched and precinct_mismatch(row["precinct"], row["violation_precinct"])
    county_mismatch_flag = matched and county_mismatch(row["county"], row["violation_county"])

    notes = []
    if null_issue_date_b:
        notes.append(ANOMALY_NULL_ISSUE_DATE_B)
    if date_mismatch:
        notes.append(ANOMALY_ISSUE_DATE_MISMATCH)
    if future_issue_date_b:
        notes.append(ANOMALY_FUTURE_ISSUE_DATE_B)
    if precinct_mismatch_flag:
        notes.append(ANOMALY_PRECINCT_MISMATCH)
    if county_mismatch_flag:
        notes.append(ANOMALY_COUNTY_MISMATCH)

    return {
        "anomaly_null_issue_date_source_b": null_issue_date_b,
        "anomaly_future_issue_date_source_b": future_issue_date_b,
        "issue_date_mismatch_flag": date_mismatch,
        "precinct_mismatch_flag": precinct_mismatch_flag,
        "county_mismatch_flag": county_mismatch_flag,
        "anomaly_notes": ";".join(notes),
    }


def add_anomaly_columns(matched_df: pd.DataFrame, today: date) -> pd.DataFrame:
    """Attach one boolean column per anomaly/mismatch type, plus a combined
    `anomaly_notes` text column, to a matched+classified DataFrame (the
    output of matching.add_match_columns).
    """
    df = matched_df.copy()
    flags = df.apply(lambda row: _row_anomaly_flags(row, today), axis=1, result_type="expand")
    return pd.concat([df, flags], axis=1)


def fiscal_year_tag_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Distribution of Source B `fiscal_year` values among matched tickets,
    plus the max issue_date observed per value — the direct measurement of
    FY-rollover lag from spec 3.8.
    """
    matched = df[df["match_status"] != MATCH_UNMATCHED].copy()
    matched["_issue_date_source_b_parsed"] = matched["issue_date_source_b"].apply(_as_date)

    summary = (
        matched.groupby("fiscal_year", dropna=False)
        .agg(count=("summons_number", "count"), max_issue_date=("_issue_date_source_b_parsed", "max"))
        .reset_index()
    )
    return summary
