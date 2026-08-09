import pandas as pd
import pytest

from src.matching import (
    MATCH_HOUSE_LEVEL,
    MATCH_NO_ADDRESS,
    MATCH_STREET_ONLY,
    MATCH_UNMATCHED,
    TIER_COUNTY,
    TIER_HOUSE_NUMBER,
    TIER_PRECINCT,
    TIER_STREET_CODE,
)
from src.report import (
    RAW_COLUMNS,
    anomaly_summary,
    build_raw_report,
    match_rate_by_age_bucket,
    match_rate_by_ticket_type,
    mismatch_summary,
    overall_match_rate_table,
    precision_tier_distribution,
    write_reports,
)


def _row(summons_number, **overrides):
    row = {
        "summons_number": summons_number,
        "issue_date": "2026-07-01",
        "plate": "ABC1234",
        "issuing_agency": "TRAFFIC",
        "ticket_type": "officer",
        "precinct": "019",
        "county": "NY",
        "age_bucket": "0-30",
        "match_status": MATCH_UNMATCHED,
        "precision_tier": TIER_PRECINCT,
        "house_number": None,
        "street_name": None,
        "intersecting_street": None,
        "street_code1": None,
        "fiscal_year": None,
        "issue_date_source_b": None,
        "precinct_mismatch_flag": False,
        "county_mismatch_flag": False,
        "issue_date_mismatch_flag": False,
        "anomaly_null_issue_date_source_b": False,
        "anomaly_future_issue_date_source_b": False,
        "anomaly_notes": "",
    }
    row.update(overrides)
    return row


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        [
            _row(
                "1",
                ticket_type="officer",
                age_bucket="0-30",
                match_status=MATCH_HOUSE_LEVEL,
                precision_tier=TIER_HOUSE_NUMBER,
                fiscal_year="2026",
                issue_date_source_b="2026-07-01",
            ),
            _row(
                "2",
                ticket_type="officer",
                age_bucket="0-30",
                match_status=MATCH_STREET_ONLY,
                precision_tier=TIER_STREET_CODE,
                fiscal_year="2026",
                issue_date_source_b="2026-07-05",
            ),
            _row(
                "3",
                ticket_type="camera",
                age_bucket="30-60",
                match_status=MATCH_UNMATCHED,
                precision_tier=TIER_PRECINCT,
            ),
            _row(
                "4",
                ticket_type="camera",
                age_bucket="90+",
                match_status=MATCH_NO_ADDRESS,
                precision_tier=TIER_COUNTY,
                precinct_mismatch_flag=True,
                fiscal_year="2025",
                issue_date_source_b="2025-06-01",
            ),
            _row(
                "5",
                ticket_type="officer",
                age_bucket="90+",
                match_status=MATCH_UNMATCHED,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# build_raw_report
# ---------------------------------------------------------------------------


def test_build_raw_report_has_exact_spec_columns(sample_df):
    raw = build_raw_report(sample_df)
    assert list(raw.columns) == RAW_COLUMNS


def test_build_raw_report_renames_fiscal_year(sample_df):
    raw = build_raw_report(sample_df)
    assert raw.loc[raw["summons_number"] == "1", "fy_tag_source_b"].iloc[0] == "2026"


def test_build_raw_report_preserves_row_count(sample_df):
    raw = build_raw_report(sample_df)
    assert len(raw) == 5


# ---------------------------------------------------------------------------
# overall_match_rate_table
# ---------------------------------------------------------------------------


def test_overall_match_rate(sample_df):
    table = overall_match_rate_table(sample_df)
    assert table.loc[0, "value"] == 60.0  # 3 matched / 5 total
    assert table.loc[0, "sample_size"] == 5


def test_overall_match_rate_empty_df_is_zero():
    empty = pd.DataFrame(columns=["match_status"])
    table = overall_match_rate_table(empty)
    assert table.loc[0, "value"] == 0.0


# ---------------------------------------------------------------------------
# match_rate_by_ticket_type
# ---------------------------------------------------------------------------


def test_match_rate_by_ticket_type(sample_df):
    table = match_rate_by_ticket_type(sample_df).set_index("ticket_type")
    assert table.loc["officer", "sample_size"] == 3
    assert table.loc["officer", "match_rate_pct"] == pytest.approx(66.67, abs=0.01)
    assert table.loc["camera", "sample_size"] == 2
    assert table.loc["camera", "match_rate_pct"] == 50.0


# ---------------------------------------------------------------------------
# match_rate_by_age_bucket
# ---------------------------------------------------------------------------


def test_match_rate_by_age_bucket(sample_df):
    table = match_rate_by_age_bucket(sample_df).set_index("age_bucket")
    assert table.loc["0-30", "match_rate_pct"] == 100.0
    assert table.loc["0-30", "sample_size"] == 2
    assert table.loc["30-60", "match_rate_pct"] == 0.0
    assert table.loc["60-90", "sample_size"] == 0
    assert table.loc["60-90", "match_rate_pct"] == 0.0
    assert table.loc["90+", "match_rate_pct"] == 50.0
    assert table.loc["90+", "sample_size"] == 2


# ---------------------------------------------------------------------------
# precision_tier_distribution
# ---------------------------------------------------------------------------


def test_precision_tier_distribution_among_matched_only(sample_df):
    table = precision_tier_distribution(sample_df).set_index("precision_tier")
    assert table.loc[TIER_HOUSE_NUMBER, "count"] == 1
    assert table.loc[TIER_HOUSE_NUMBER, "pct_of_matched"] == pytest.approx(33.33, abs=0.01)
    assert table.loc[TIER_STREET_CODE, "count"] == 1
    assert table.loc[TIER_COUNTY, "count"] == 1
    assert table.loc[TIER_PRECINCT, "count"] == 0  # row 3's precinct tier is unmatched, excluded


# ---------------------------------------------------------------------------
# mismatch_summary
# ---------------------------------------------------------------------------


def test_mismatch_summary_among_matched_only(sample_df):
    table = mismatch_summary(sample_df).set_index("mismatch_type")
    assert table.loc["precinct_mismatch", "count"] == 1
    assert table.loc["precinct_mismatch", "pct_of_matched"] == pytest.approx(33.33, abs=0.01)
    assert table.loc["county_mismatch", "count"] == 0


# ---------------------------------------------------------------------------
# anomaly_summary
# ---------------------------------------------------------------------------


def test_anomaly_summary_pct_of_full_sample(sample_df):
    table = anomaly_summary(sample_df).set_index("anomaly_type")
    assert table.loc["precinct_mismatch", "count"] == 1
    assert table.loc["precinct_mismatch", "pct_of_sample"] == 20.0  # 1/5
    assert table.loc["county_mismatch", "count"] == 0


# ---------------------------------------------------------------------------
# write_reports (integration)
# ---------------------------------------------------------------------------


def test_write_reports_creates_both_files_with_expected_content(sample_df, tmp_path):
    raw_path = tmp_path / "match_rate_raw.csv"
    summary_path = tmp_path / "match_rate_summary.csv"

    write_reports(sample_df, raw_path, summary_path)

    assert raw_path.exists()
    assert summary_path.exists()

    raw = pd.read_csv(raw_path, dtype={"summons_number": str})
    assert len(raw) == 5
    assert list(raw.columns) == RAW_COLUMNS

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "# Overall Match Rate" in summary_text
    assert "# Match Rate by Ticket Type" in summary_text
    assert "# Fiscal Year Tag Distribution (Source B)" in summary_text
