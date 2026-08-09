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
            "issue_date": "07/01/2026",  # real Source A format: MM/DD/YYYY text
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


def test_fetch_source_a_sample_normalizes_mmddyyyy_issue_date_to_iso():
    rows = [{"summons_number": "1", "issue_date": "07/04/2026"}]
    client = make_client(rows)

    df = fetch_source_a_sample(client, date(2026, 7, 1), date(2026, 7, 31))

    assert df.loc[0, "issue_date"] == "2026-07-04"


def test_fetch_source_a_sample_blank_issue_date_becomes_none():
    rows = [{"summons_number": "1", "issue_date": None}]
    client = make_client(rows)

    df = fetch_source_a_sample(client, date(2026, 7, 1), date(2026, 7, 31))

    assert df.loc[0, "issue_date"] is None


def test_fetch_source_a_sample_queries_by_mmddyyyy_date_list_not_iso_range():
    client = make_client([])

    fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 3))

    args, _ = client.get_all.call_args
    assert args[0] == SOURCE_A_DATASET_ID
    where = args[1]["$where"]
    assert "issue_date in (" in where
    assert "'01/01/2026'" in where
    assert "'01/02/2026'" in where
    assert "'01/03/2026'" in where
    # the old ISO `between` query silently matched nothing against this
    # source's real (text, MM/DD/YYYY) issue_date column
    assert "between" not in where


def test_fetch_source_a_sample_chunks_wide_date_ranges():
    client = make_client([])

    fetch_source_a_sample(
        client, date(2026, 1, 1), date(2026, 4, 11), date_chunk_size=100
    )

    # 101 days across the range, chunked at 100 -> 2 requests
    assert client.get_all.call_count == 2


def test_fetch_source_a_sample_with_limit_queries_one_day_at_a_time():
    """A capped sample must spread across days, not fill up from whichever
    date Socrata returns first — otherwise the whole sample can land in a
    single age bucket (confirmed live: a 300-row cap over a 160-day range
    came back 300/300 from a single day).
    """
    client = make_client([])

    fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 5), limit=5)

    assert client.get_all.call_count == 5
    where_clauses = [call.args[1]["$where"] for call in client.get_all.call_args_list]
    assert "'01/01/2026'" in where_clauses[0]
    assert "'01/02/2026'" in where_clauses[1]
    assert "'01/05/2026'" in where_clauses[4]


def test_fetch_source_a_sample_with_limit_caps_rows_per_day_evenly():
    client = MagicMock()
    client.get_all.return_value = [{"summons_number": "1", "issue_date": "01/01/2026"}]

    fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 5), limit=10)

    # 10 rows over 5 days -> 2 per day
    for call in client.get_all.call_args_list:
        assert call.kwargs["max_rows"] == 2


def test_fetch_source_a_sample_with_limit_stops_once_cap_reached():
    client = MagicMock()
    # each day "returns" more than its per-day share, so the cap should be
    # hit and the loop should stop well before every day is queried
    client.get_all.return_value = [
        {"summons_number": str(i), "issue_date": "01/01/2026"} for i in range(5)
    ]

    df = fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 30), limit=10)

    assert len(df) == 10
    assert client.get_all.call_count == 2  # 5 + 5 = 10, cap hit after day 2


def test_fetch_source_a_sample_includes_plate_filter_when_given():
    client = make_client([])

    fetch_source_a_sample(client, date(2026, 1, 1), date(2026, 1, 1), plates=["ABC1234"])

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


def test_fetch_source_b_does_not_send_select_clause():
    # No $select: historical FY dataset IDs don't share the current
    # dataset's schema (confirmed live — the FY2023 ID has no fiscal_year
    # column at all), so a fixed field list would 400 against them.
    client = MagicMock()
    client.get_all.return_value = []

    fetch_source_b(client, {"869v-vr48": ["1"]})

    _, params = client.get_all.call_args[0]
    assert "$select" not in params


def test_fetch_source_b_missing_column_in_historical_dataset_becomes_null():
    # Simulates querying a historical dataset that has no fiscal_year
    # column: that key is simply absent from the row Socrata returns.
    client = MagicMock()
    client.get_all.return_value = [
        {
            "summons_number": "1",
            "issue_date": "2023-02-01T00:00:00.000",
            "house_number": "123",
            "street_name": "MAIN ST",
            # no "fiscal_year" key at all
        }
    ]

    result = fetch_source_b(client, {"869v-vr48": ["1"]})

    assert pd.isna(result.loc[0, "fiscal_year"])
    assert result.loc[0, "house_number"] == "123"


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
