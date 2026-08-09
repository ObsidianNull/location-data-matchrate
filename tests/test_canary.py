import json
import sqlite3
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.canary import (
    CanaryFailure,
    check_canaries,
    get_canaries,
    get_connection,
    init_schema,
    record_run,
    select_canary_candidates,
    store_canaries,
)
from src.matching import MATCH_HOUSE_LEVEL, MATCH_UNMATCHED


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# schema / connection
# ---------------------------------------------------------------------------


def test_init_schema_creates_both_tables(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "canaries" in tables
    assert "run_history" in tables


def test_get_connection_creates_db_file_and_schema(tmp_path):
    db_path = tmp_path / "sub" / "run.db"
    connection = get_connection(db_path)
    try:
        assert db_path.exists()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "canaries" in tables
        assert "run_history" in tables
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# check_canaries
# ---------------------------------------------------------------------------


def test_check_canaries_no_canaries_does_nothing(conn):
    client = MagicMock()
    check_canaries(conn, client)
    client.get_all.assert_not_called()


def test_check_canaries_passes_when_all_still_match(conn):
    store_canaries(
        conn,
        [{"summons_number": "1234567890", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL}],
    )
    client = MagicMock()
    client.get_all.return_value = [{"summons_number": "1234567890"}]

    check_canaries(conn, client)  # should not raise

    args, _ = client.get_all.call_args
    assert args[0] == "pvqr-7yc4"
    assert "1234567890" in args[1]["$where"]


def test_check_canaries_raises_canary_failure_when_a_canary_stops_matching(conn):
    store_canaries(
        conn,
        [{"summons_number": "1234567890", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL}],
    )
    client = MagicMock()
    client.get_all.return_value = []  # rotated dataset -> no longer found

    with pytest.raises(CanaryFailure, match="1234567890"):
        check_canaries(conn, client)


def test_check_canaries_checks_each_canary_against_its_own_dataset_id(conn):
    store_canaries(
        conn,
        [
            {"summons_number": "1", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL},
            {"summons_number": "2", "dataset_id": "m5vz-tzqv", "match_status": MATCH_HOUSE_LEVEL},
        ],
    )
    client = MagicMock()
    client.get_all.return_value = [{"summons_number": "x"}]

    check_canaries(conn, client)

    queried_datasets = {call.args[0] for call in client.get_all.call_args_list}
    assert queried_datasets == {"pvqr-7yc4", "m5vz-tzqv"}


# ---------------------------------------------------------------------------
# select_canary_candidates / store_canaries / get_canaries
# ---------------------------------------------------------------------------


def test_select_canary_candidates_only_picks_matched_rows():
    df = pd.DataFrame(
        [
            {"summons_number": "1", "match_status": MATCH_HOUSE_LEVEL, "source_b_dataset_id": "pvqr-7yc4"},
            {"summons_number": "2", "match_status": MATCH_UNMATCHED, "source_b_dataset_id": None},
            {"summons_number": "3", "match_status": MATCH_HOUSE_LEVEL, "source_b_dataset_id": "pvqr-7yc4"},
        ]
    )

    candidates = select_canary_candidates(df, n=5)

    assert {c["summons_number"] for c in candidates} == {"1", "3"}


def test_select_canary_candidates_respects_n():
    df = pd.DataFrame(
        [
            {"summons_number": str(i), "match_status": MATCH_HOUSE_LEVEL, "source_b_dataset_id": "pvqr-7yc4"}
            for i in range(10)
        ]
    )
    candidates = select_canary_candidates(df, n=3)
    assert len(candidates) == 3


def test_store_canaries_then_get_canaries_round_trips(conn):
    store_canaries(
        conn,
        [
            {"summons_number": "1", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL},
            {"summons_number": "2", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL},
        ],
    )

    canaries = get_canaries(conn)
    assert {c["summons_number"] for c in canaries} == {"1", "2"}


def test_store_canaries_replaces_previous_set(conn):
    store_canaries(conn, [{"summons_number": "old", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL}])
    store_canaries(conn, [{"summons_number": "new", "dataset_id": "pvqr-7yc4", "match_status": MATCH_HOUSE_LEVEL}])

    canaries = get_canaries(conn)
    assert [c["summons_number"] for c in canaries] == ["new"]


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------


def test_record_run_writes_a_row_and_returns_run_id(conn):
    run_id = record_run(conn, sample_size=100, overall_match_rate=0.82, summary_json=json.dumps({"ok": True}))

    row = conn.execute(
        "SELECT sample_size, overall_match_rate, summary_json FROM run_history WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    assert row == (100, 0.82, json.dumps({"ok": True}))


def test_record_run_multiple_runs_accumulate(conn):
    record_run(conn, 10, 0.5, "{}")
    record_run(conn, 20, 0.6, "{}")

    count = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
    assert count == 2
