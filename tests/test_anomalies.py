from datetime import date

import pandas as pd
import pytest

from src.anomalies import (
    add_anomaly_columns,
    county_mismatch,
    fiscal_year_tag_summary,
    is_future_issue_date_b,
    is_null_issue_date_b,
    issue_date_mismatch,
    normalize_precinct,
    precinct_mismatch,
)
from src.matching import MATCH_HOUSE_LEVEL, MATCH_UNMATCHED

TODAY = date(2026, 8, 8)


# ---------------------------------------------------------------------------
# normalize_precinct / precinct_mismatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("019", "19"), ("19", "19"), (" 019 ", "19"), ("000", "0"), (None, None), ("", None)],
)
def test_normalize_precinct(raw, expected):
    assert normalize_precinct(raw) == expected


def test_precinct_mismatch_true_when_different():
    assert precinct_mismatch("019", "020")


def test_precinct_mismatch_false_when_same_after_normalization():
    assert not precinct_mismatch("019", "19")


def test_precinct_mismatch_false_when_either_side_blank():
    assert not precinct_mismatch(None, "020")
    assert not precinct_mismatch("019", None)


# ---------------------------------------------------------------------------
# county_mismatch
# ---------------------------------------------------------------------------


def test_county_mismatch_true_when_different_boroughs():
    assert county_mismatch("NY", "BK")


def test_county_mismatch_false_when_same_after_normalization():
    assert not county_mismatch("K", "BK")
    assert not county_mismatch("BX", "Bronx")


def test_county_mismatch_false_when_either_side_blank():
    assert not county_mismatch(None, "BK")


# ---------------------------------------------------------------------------
# issue_date_mismatch / null / future
# ---------------------------------------------------------------------------


def test_issue_date_mismatch_true_when_different():
    assert issue_date_mismatch("2026-07-01", "2026-07-02")


def test_issue_date_mismatch_false_when_same():
    assert not issue_date_mismatch("2026-07-01T00:00:00.000", "2026-07-01")


def test_issue_date_mismatch_false_when_either_side_blank():
    assert not issue_date_mismatch(None, "2026-07-01")


def test_is_null_issue_date_b_true_for_none_and_empty():
    assert is_null_issue_date_b(None)
    assert is_null_issue_date_b("")


def test_is_null_issue_date_b_false_when_present():
    assert not is_null_issue_date_b("2026-07-01")


def test_is_future_issue_date_b_true_when_after_today():
    assert is_future_issue_date_b("2026-08-09", TODAY)


def test_is_future_issue_date_b_false_when_today_or_past():
    assert not is_future_issue_date_b("2026-08-08", TODAY)
    assert not is_future_issue_date_b("2026-01-01", TODAY)


def test_is_future_issue_date_b_false_when_null():
    assert not is_future_issue_date_b(None, TODAY)


# ---------------------------------------------------------------------------
# add_anomaly_columns
# ---------------------------------------------------------------------------


def _row(summons_number, match_status=MATCH_HOUSE_LEVEL, **overrides):
    row = {
        "summons_number": summons_number,
        "match_status": match_status,
        "issue_date": "2026-07-01",
        "issue_date_source_b": "2026-07-01",
        "precinct": "019",
        "violation_precinct": "019",
        "county": "NY",
        "violation_county": "NY",
    }
    row.update(overrides)
    return row


def test_add_anomaly_columns_clean_matched_row_has_no_flags():
    df = pd.DataFrame([_row("1")])

    result = add_anomaly_columns(df, TODAY)

    row = result.iloc[0]
    assert not row["anomaly_null_issue_date_source_b"]
    assert not row["anomaly_future_issue_date_source_b"]
    assert not row["issue_date_mismatch_flag"]
    assert not row["precinct_mismatch_flag"]
    assert not row["county_mismatch_flag"]
    assert row["anomaly_notes"] == ""


def test_add_anomaly_columns_flags_all_types_on_a_bad_row():
    df = pd.DataFrame(
        [
            _row(
                "2",
                issue_date_source_b=None,
                issue_date="2026-07-01",
                precinct="019",
                violation_precinct="020",
                county="NY",
                violation_county="BK",
            )
        ]
    )

    result = add_anomaly_columns(df, TODAY)
    row = result.iloc[0]

    assert row["anomaly_null_issue_date_source_b"]
    assert row["precinct_mismatch_flag"]
    assert row["county_mismatch_flag"]
    # issue_date_mismatch can't independently fire here since B's date is
    # null (nothing to compare against) — null takes precedence, not mismatch.
    assert not row["issue_date_mismatch_flag"]
    assert "null_issue_date_source_b" in row["anomaly_notes"]
    assert "precinct_mismatch" in row["anomaly_notes"]
    assert "county_mismatch" in row["anomaly_notes"]


def test_add_anomaly_columns_future_issue_date():
    df = pd.DataFrame([_row("3", issue_date_source_b="2026-12-25")])

    result = add_anomaly_columns(df, TODAY)
    row = result.iloc[0]

    assert row["anomaly_future_issue_date_source_b"]
    assert "future_issue_date_source_b" in row["anomaly_notes"]


def test_add_anomaly_columns_unmatched_row_has_no_flags_even_with_bad_looking_data():
    # Unmatched rows have no real Source B data — any non-null-looking
    # value here is an artifact, not a real anomaly, so nothing should fire.
    df = pd.DataFrame(
        [
            _row(
                "4",
                match_status=MATCH_UNMATCHED,
                issue_date_source_b=None,
                violation_precinct="999",
                violation_county="ZZ",
            )
        ]
    )

    result = add_anomaly_columns(df, TODAY)
    row = result.iloc[0]

    assert not row["anomaly_null_issue_date_source_b"]
    assert not row["precinct_mismatch_flag"]
    assert not row["county_mismatch_flag"]
    assert row["anomaly_notes"] == ""


def test_add_anomaly_columns_preserves_row_count_and_order():
    df = pd.DataFrame([_row("1"), _row("2"), _row("3")])
    result = add_anomaly_columns(df, TODAY)
    assert list(result["summons_number"]) == ["1", "2", "3"]


def test_add_anomaly_columns_empty_dataframe_still_has_all_columns():
    # A zero-row sample (e.g. no tickets in the queried date range) must not
    # crash downstream report building for lack of these columns.
    df = pd.DataFrame([_row("1")]).iloc[0:0]
    result = add_anomaly_columns(df, TODAY)

    assert len(result) == 0
    for col in [
        "anomaly_null_issue_date_source_b",
        "anomaly_future_issue_date_source_b",
        "issue_date_mismatch_flag",
        "precinct_mismatch_flag",
        "county_mismatch_flag",
        "anomaly_notes",
    ]:
        assert col in result.columns


# ---------------------------------------------------------------------------
# fiscal_year_tag_summary
# ---------------------------------------------------------------------------


def test_fiscal_year_tag_summary_counts_and_max_date_per_tag():
    df = pd.DataFrame(
        [
            _row("1", fiscal_year="2026", issue_date_source_b="2026-07-01"),
            _row("2", fiscal_year="2026", issue_date_source_b="2026-08-01"),
            _row("3", fiscal_year="2025", issue_date_source_b="2025-06-15"),
            _row("4", match_status=MATCH_UNMATCHED, fiscal_year=None, issue_date_source_b=None),
        ]
    )

    summary = fiscal_year_tag_summary(df).set_index("fiscal_year")

    assert summary.loc["2026", "count"] == 2
    assert summary.loc["2026", "max_issue_date"] == date(2026, 8, 1)
    assert summary.loc["2025", "count"] == 1
    assert summary.loc["2025", "max_issue_date"] == date(2025, 6, 15)
    assert "2026" in summary.index and "2025" in summary.index
    # the unmatched row's fiscal_year (None) shouldn't create a bogus tag
    assert len(summary) == 2
