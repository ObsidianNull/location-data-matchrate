"""
SQLite-backed canary tracking and run-history persistence.

Canary summons numbers are known-matched tickets from a prior run, stored
so the next run can re-check them first, against the same dataset ID they
were originally found in. If a previously-matching canary stops matching,
that's a strong signal the Source B dataset ID has been rotated or
rewritten (NYC is known to reuse this same ID for different fiscal years).

Decision: a canary failure hard-stops the run. If the dataset ID was
rotated, every other summons number fetched in this run is suspect too —
continuing and reporting a match rate anyway would silently misinform the
founder conversation this script exists to inform.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.matching import MATCH_UNMATCHED
from src.socrata_client import build_in_clause

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "run.db"
DEFAULT_CANARY_COUNT = 5


class CanaryFailure(Exception):
    """Raised when a previously-matched canary summons number no longer
    matches. Callers should treat this as a hard stop, not a warning.
    """


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS canaries (
            summons_number TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            last_known_match_status TEXT NOT NULL,
            stored_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_history (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            overall_match_rate REAL NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )
    conn.commit()


def get_canaries(conn: sqlite3.Connection) -> list[dict]:
    cursor = conn.execute(
        "SELECT summons_number, dataset_id, last_known_match_status FROM canaries"
    )
    return [
        {"summons_number": row[0], "dataset_id": row[1], "last_known_match_status": row[2]}
        for row in cursor.fetchall()
    ]


def check_canaries(conn: sqlite3.Connection, client) -> None:
    """Re-query every stored canary against its recorded dataset ID.

    Raises CanaryFailure on the first canary that no longer comes back,
    naming the summons number and dataset so the failure is immediately
    actionable. Does nothing on a fresh DB with no canaries yet (first run).
    """
    canaries = get_canaries(conn)
    if not canaries:
        logger.info("No canaries on file yet (first run) — skipping canary check.")
        return

    for canary in canaries:
        summons_number = canary["summons_number"]
        dataset_id = canary["dataset_id"]
        where_clause = f"summons_number in ({build_in_clause([summons_number])})"

        rows = client.get_all(dataset_id, {"$where": where_clause})

        if not rows:
            raise CanaryFailure(
                f"Canary summons_number={summons_number!r} no longer matches in "
                f"dataset {dataset_id!r}. This dataset ID may have been rotated "
                f"or rewritten — the rest of this run cannot be trusted without "
                f"investigating that first."
            )

    logger.info("All %d canaries checked out.", len(canaries))


def select_canary_candidates(matched_df, n: int = DEFAULT_CANARY_COUNT) -> list[dict]:
    """Pick up to n known-matched summons numbers from this run's results
    to store as the next run's canaries, along with the dataset ID each was
    actually found in.
    """
    matched = matched_df[matched_df["match_status"] != MATCH_UNMATCHED]
    picked = matched.head(n)
    return [
        {
            "summons_number": row["summons_number"],
            "dataset_id": row["source_b_dataset_id"],
            "match_status": row["match_status"],
        }
        for _, row in picked.iterrows()
    ]


def store_canaries(conn: sqlite3.Connection, canary_candidates: list[dict]) -> None:
    """Replace the stored canary set with fresh known-matched summons
    numbers from the run that just completed successfully.
    """
    now = datetime.now(UTC).isoformat()
    conn.execute("DELETE FROM canaries")
    conn.executemany(
        """
        INSERT INTO canaries (summons_number, dataset_id, last_known_match_status, stored_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (c["summons_number"], c["dataset_id"], c["match_status"], now)
            for c in canary_candidates
        ],
    )
    conn.commit()


def record_run(
    conn: sqlite3.Connection,
    sample_size: int,
    overall_match_rate: float,
    summary_json: str,
) -> int:
    """Write one row to run_history and return its run_id."""
    now = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO run_history (run_timestamp, sample_size, overall_match_rate, summary_json)
        VALUES (?, ?, ?, ?)
        """,
        (now, sample_size, overall_match_rate, summary_json),
    )
    conn.commit()
    return cursor.lastrowid
