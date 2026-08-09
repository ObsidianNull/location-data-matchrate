from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.sampling import (
    SOURCE_A_DATASET_ID,
    dataset_id_for_ticket,
    fetch_source_a_sample,
    fetch_source_b,
    group_summons_numbers_by_dataset,
    merge_source_a_and_b,
    nyc_fiscal_year_label,
)

DATASETS = {
    "current": "pvqr-7yc4",
    "history": {
        "FY2025": "m5vz-tzqv",
        "FY2023": "869v-vr48",
        "FY2014": "jt7v-77mi",
    },
}


def make_client(rows):
    client = MagicMock()
    client.get_all.return_value = rows
    return client


# ---------------------------------------------------------------------------
# fetch_source_a_sample
# ---------------------------------------------------------------------------


def test_fetch_source_a_sample_forces_summons_number_to_str():
    rows = [
        {
            "summons_number": "0012345678",  # leading zero, as Socrata returns it
            "plate": "ABC1234",
            "state": "NY",
            "issue_date": "2026-07-01T00:00:00.000",
            "violation": "NO PARKING",
            "precinct": "019",
            "county": "NY",
            "issuing_agency": "TRAFFIC",
            "judgment_entry_date": None,
        }
    ]
    client = make_client(rows)

    df = fetch_source_a_sample(client, date(2026, 7, 1), date(2026, 7, 31))

    assert isinstance(df.loc[0, "summons_number"], str)
    assert df.loc[0, "summons_number"] == "0012345678"


def test_fetch_source_a_sample_queries_correct_dataset_with_date_range():
    client = make_client([])

    fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 31))

    args, _ = client.get_all.call_args
    assert args[0] == SOURCE_A_DATASET_ID
    where = args[1]["$where"]
    assert "2026-01-01T00:00:00" in where
    assert "2026-01-31T23:59:59" in where


def test_fetch_source_a_sample_includes_plate_filter_when_given():
    client = make_client([])

    fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 31), plates=["ABC1234"])

    _, params = client.get_all.call_args[0]
    assert "plate in ('ABC1234')" in params["$where"]


def test_fetch_source_a_sample_returns_expected_columns():
    client = make_client([])

    df = fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 31))

    assert list(df.columns) == [
        "summons_number",
        "plate",
        "state",
        "issue_date",
        "violation",
        "precinct",
        "county",
        "issuing_agency",
        "judgment_entry_date",
    ]


# ---------------------------------------------------------------------------
# nyc_fiscal_year_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "issue_date,expected",
    [
        (date(2025, 8, 1), "FY2026"),
        (date(2025, 7, 1), "FY2026"),
        (date(2025, 6, 30), "FY2025"),
        (date(2023, 5, 1), "FY2023"),
        (date(2013, 7, 15), "FY2014"),
    ],
)
def test_nyc_fiscal_year_label(issue_date, expected):
    assert nyc_fiscal_year_label(issue_date) == expected


# ---------------------------------------------------------------------------
# dataset_id_for_ticket
# ---------------------------------------------------------------------------


def test_dataset_id_for_ticket_recent_uses_current():
    today = date(2026, 8, 8)
    issue_date = date(2026, 7, 1)  # 38 days old
    assert dataset_id_for_ticket(issue_date, today, DATASETS) == "pvqr-7yc4"


def test_dataset_id_for_ticket_old_known_fy_uses_history():
    today = date(2026, 8, 8)
    issue_date = date(2023, 1, 1)  # well over a year old, maps to FY2023
    assert dataset_id_for_ticket(issue_date, today, DATASETS) == "869v-vr48"


def test_dataset_id_for_ticket_old_unknown_fy_returns_none():
    today = date(2026, 8, 8)
    issue_date = date(2019, 1, 1)  # maps to FY2019, not in our history table
    assert dataset_id_for_ticket(issue_date, today, DATASETS) is None


# ---------------------------------------------------------------------------
# group_summons_numbers_by_dataset
# ---------------------------------------------------------------------------


def test_group_summons_numbers_by_dataset_splits_recent_and_historical():
    today = date(2026, 8, 8)
    sample_df = pd.DataFrame(
        [
            {"summons_number": "1", "issue_date": "2026-07-01T00:00:00.000"},
            {"summons_number": "2", "issue_date": "2023-01-01T00:00:00.000"},
            {"summons_number": "3", "issue_date": "2019-01-01T00:00:00.000"},
        ]
    )

    grouped = group_summons_numbers_by_dataset(sample_df, today, DATASETS)

    assert grouped == {
        "pvqr-7yc4": ["1"],
        "869v-vr48": ["2"],
    }


# ---------------------------------------------------------------------------
# fetch_source_b
# ---------------------------------------------------------------------------


def test_fetch_source_b_batches_per_dataset_and_forces_str():
    client = MagicMock()
    client.get_all.return_value = [
        {
            "summons_number": "0000000001",
            "fiscal_year": "2026",
            "issue_date": "2026-07-01T00:00:00.000",
            "house_number": "123",
            "street_name": "MAIN ST",
            "intersecting_street": None,
            "street_code1": "12345",
            "street_code2": None,
            "street_code3": None,
            "violation_precinct": "019",
            "violation_county": "NY",
            "issuing_agency": "TRAFFIC",
        }
    ]

    result = fetch_source_b(client, {"pvqr-7yc4": ["0000000001"]})

    assert client.get_all.call_count == 1
    assert result.loc[0, "summons_number"] == "0000000001"


def test_fetch_source_b_missing_summons_number_absent_not_dropped_silently():
    client = MagicMock()
    # Queried for two summons numbers, only one comes back — the missing one
    # should simply not appear in the result (merge handles the "no match").
    client.get_all.return_value = [{"summons_number": "1", "fiscal_year": "2026"}]

    result = fetch_source_b(client, {"pvqr-7yc4": ["1", "2"]})

    assert list(result["summons_number"]) == ["1"]


def test_fetch_source_b_empty_map_returns_empty_dataframe():
    client = MagicMock()
    result = fetch_source_b(client, {})
    assert result.empty
    assert client.get_all.call_count == 0


# ---------------------------------------------------------------------------
# merge_source_a_and_b
# ---------------------------------------------------------------------------


def test_merge_left_join_keeps_unmatched_source_a_rows_with_nulls():
    source_a = pd.DataFrame(
        [
            {"summons_number": "1", "issue_date": "2026-07-01", "plate": "ABC1234"},
            {"summons_number": "2", "issue_date": "2026-07-02", "plate": "XYZ9999"},
        ]
    )
    source_b = pd.DataFrame(
        [{"summons_number": "1", "issue_date": "2026-07-01", "house_number": "123"}]
    )

    merged = merge_source_a_and_b(source_a, source_b)

    assert len(merged) == 2
    row2 = merged[merged["summons_number"] == "2"].iloc[0]
    assert pd.isna(row2["house_number"])


def test_merge_renames_source_b_issue_date_to_avoid_collision():
    source_a = pd.DataFrame([{"summons_number": "1", "issue_date": "2026-07-01"}])
    source_b = pd.DataFrame([{"summons_number": "1", "issue_date": "2026-07-05"}])

    merged = merge_source_a_and_b(source_a, source_b)

    assert merged.loc[0, "issue_date"] == "2026-07-01"
    assert merged.loc[0, "issue_date_source_b"] == "2026-07-05"
