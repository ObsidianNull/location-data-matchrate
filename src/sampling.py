"""
Pulls a sample of tickets from Source A (Open Parking and Camera Violations,
nc67-uf89) and batch-fetches the matching Source B (Parking Violations
Issued) rows for those same summons numbers.
"""

import logging
from datetime import date, datetime

import pandas as pd

from src.config import ConfigError, get_dataset_id
from src.socrata_client import SocrataClient, build_in_clause, chunk_ids

logger = logging.getLogger(__name__)

SOURCE_A_DATASET_ID = "nc67-uf89"

SOURCE_A_FIELDS = [
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

SOURCE_B_FIELDS = [
    "summons_number",
    "fiscal_year",
    "issue_date",
    "house_number",
    "street_name",
    "intersecting_street",
    "street_code1",
    "street_code2",
    "street_code3",
    "violation_precinct",
    "violation_county",
    "issuing_agency",
]

# Tickets this recent are assumed to still be covered by the "current"
# fiscal-year dataset. Older tickets need a historical FY dataset ID looked
# up from config — see dataset_id_for_ticket().
RECENT_TICKET_WINDOW_DAYS = 365


def fetch_source_a_sample(
    client: SocrataClient,
    start_date: date,
    end_date: date,
    plates: list[str] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Pull a sample of tickets from Source A over [start_date, end_date],
    optionally narrowed to a list of plates.

    `summons_number` is cast to `str` immediately on read — before it ever
    touches pandas — so leading zeros can never be silently lost to an int
    cast further down the pipeline.
    """
    where_parts = [
        f"issue_date between '{start_date.isoformat()}T00:00:00' "
        f"and '{end_date.isoformat()}T23:59:59'"
    ]
    if plates:
        where_parts.append(f"plate in ({build_in_clause(plates)})")

    params = {"$select": ",".join(SOURCE_A_FIELDS), "$where": " AND ".join(where_parts)}

    rows = client.get_all(SOURCE_A_DATASET_ID, params)
    logger.info(
        "Fetched %d tickets from Source A (%s to %s)", len(rows), start_date, end_date
    )

    df = pd.DataFrame(rows, columns=SOURCE_A_FIELDS)
    df["summons_number"] = df["summons_number"].astype(str)

    if limit is not None:
        df = df.head(limit).reset_index(drop=True)

    return df


def nyc_fiscal_year_label(issue_date: date) -> str:
    """NYC's fiscal year runs July 1 - June 30, labeled by the year it ends
    in (e.g. July 2025 - June 2026 is "FY2026").
    """
    fy_year = issue_date.year + 1 if issue_date.month >= 7 else issue_date.year
    return f"FY{fy_year}"


def dataset_id_for_ticket(
    issue_date: date,
    today: date,
    datasets: dict,
    recent_window_days: int = RECENT_TICKET_WINDOW_DAYS,
) -> str | None:
    """Pick which Source B dataset ID should hold a ticket with this
    issue_date. Recent tickets are queried against "current"; older ones
    need a historical FY dataset ID, which we may not have on file — in
    that case this returns None rather than guessing or raising, and the
    caller drops that ticket from the Source B fetch (it'll surface
    downstream as unmatched, which is honest: we genuinely can't check it).
    """
    age_days = (today - issue_date).days
    if age_days <= recent_window_days:
        return get_dataset_id("current", datasets)

    label = nyc_fiscal_year_label(issue_date)
    try:
        return get_dataset_id(label, datasets)
    except ConfigError:
        logger.warning(
            "No known Source B dataset ID for %s (issue_date=%s) — skipping "
            "Source B lookup for this ticket.",
            label,
            issue_date,
        )
        return None


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def group_summons_numbers_by_dataset(
    sample_df: pd.DataFrame,
    today: date,
    datasets: dict,
) -> dict[str, list[str]]:
    """Group a Source A sample's summons numbers by which Source B dataset
    ID should be queried for each, based on issue_date age. Tickets whose
    fiscal year has no known dataset ID are left out of the map (and
    logged) rather than causing the whole batch to fail.
    """
    grouped: dict[str, list[str]] = {}

    for _, row in sample_df.iterrows():
        issue_date = _as_date(row["issue_date"])
        dataset_id = dataset_id_for_ticket(issue_date, today, datasets)
        if dataset_id is None:
            continue
        grouped.setdefault(dataset_id, []).append(str(row["summons_number"]))

    return grouped


def fetch_source_b(
    client: SocrataClient,
    summons_numbers_by_dataset: dict[str, list[str]],
    chunk_size: int = 500,
) -> pd.DataFrame:
    """Batch-fetch Source B rows for summons numbers, grouped by which
    dataset ID they need to be queried against.

    A summons number with no match in any chunk simply doesn't appear in
    the result — merge_source_a_and_b() left-joins this back against the
    full Source A sample so "no row" becomes an explicit null, not a
    silent drop.

    Each row is tagged with `source_b_dataset_id` — which dataset ID it was
    actually found in — so a later step (canary selection) knows which
    dataset to re-query for that specific summons number.
    """
    all_rows: list[dict] = []

    for dataset_id, summons_numbers in summons_numbers_by_dataset.items():
        for chunk in chunk_ids(summons_numbers, chunk_size=chunk_size):
            where_clause = f"summons_number in ({build_in_clause(chunk)})"
            params = {"$select": ",".join(SOURCE_B_FIELDS), "$where": where_clause}
            rows = client.get_all(dataset_id, params)
            for row in rows:
                row["source_b_dataset_id"] = dataset_id
            all_rows.extend(rows)
            logger.info(
                "Source B dataset %s: matched %d/%d summons numbers in this batch",
                dataset_id,
                len(rows),
                len(chunk),
            )

    df = pd.DataFrame(all_rows, columns=[*SOURCE_B_FIELDS, "source_b_dataset_id"])
    if not df.empty:
        df["summons_number"] = df["summons_number"].astype(str)
    return df


def merge_source_a_and_b(source_a_df: pd.DataFrame, source_b_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join Source B onto Source A, keyed on summons_number. Tickets
    with no Source B match keep their Source A columns and get null for
    every Source B-only column. Both frames have an `issue_date` column;
    Source A's keeps that name, Source B's becomes `issue_date_source_b`.
    """
    return source_a_df.merge(
        source_b_df,
        on="summons_number",
        how="left",
        suffixes=("", "_source_b"),
    )
