"""
Pulls a sample of tickets from Source A (Open Parking and Camera Violations,
nc67-uf89) and batch-fetches the matching Source B (Parking Violations
Issued) rows for those same summons numbers.
"""

import logging
from datetime import date, datetime, timedelta

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


SOURCE_A_DATE_CHUNK_SIZE = 100


def _mmddyyyy(d: date) -> str:
    """nc67-uf89's issue_date column is `text` on Socrata's side, formatted
    MM/DD/YYYY (confirmed against the live dataset — it is NOT a
    floating_timestamp, so ISO date literals in a $where clause silently
    match zero rows instead of erroring). Every query against it has to
    speak this format.
    """
    return d.strftime("%m/%d/%Y")


def _date_range_inclusive(start_date: date, end_date: date) -> list[date]:
    num_days = (end_date - start_date).days
    return [start_date + timedelta(days=i) for i in range(num_days + 1)]


def parse_source_a_issue_date(raw_value) -> str | None:
    """Parse Source A's MM/DD/YYYY text issue_date into an ISO date string
    (YYYY-MM-DD), so every downstream consumer can keep assuming ISO dates
    without knowing about this source's on-disk format.
    """
    if raw_value is None or (isinstance(raw_value, str) and raw_value.strip() == ""):
        return None
    parsed = datetime.strptime(str(raw_value).strip(), "%m/%d/%Y").date()
    return parsed.isoformat()


def fetch_source_a_sample(
    client: SocrataClient,
    start_date: date,
    end_date: date,
    plates: list[str] | None = None,
    limit: int | None = None,
    date_chunk_size: int = SOURCE_A_DATE_CHUNK_SIZE,
) -> pd.DataFrame:
    """Pull a sample of tickets from Source A over [start_date, end_date],
    optionally narrowed to a list of plates.

    Since issue_date is text (MM/DD/YYYY) rather than a real timestamp
    column, a date *range* can't be expressed with `between` — instead this
    enumerates every date in the range and queries `issue_date in (...)`.

    `summons_number` is cast to `str` immediately on read — before it ever
    touches pandas — so leading zeros can never be silently lost to an int
    cast further down the pipeline. `issue_date` is normalized from
    MM/DD/YYYY to an ISO date string for the same reason: so every
    downstream module can assume one consistent format.
    """
    all_dates = _date_range_inclusive(start_date, end_date)

    if limit is None:
        # No cap: batch several days per request (chunked so a wide range
        # doesn't blow past URL length limits) — every date is fetched in
        # full, so there's no risk of skew toward whichever date happens to
        # come back first.
        date_strings = [_mmddyyyy(d) for d in all_dates]
        all_rows: list[dict] = []
        for date_chunk in chunk_ids(date_strings, chunk_size=date_chunk_size):
            where_parts = [f"issue_date in ({build_in_clause(date_chunk)})"]
            if plates:
                where_parts.append(f"plate in ({build_in_clause(plates)})")
            params = {"$select": ",".join(SOURCE_A_FIELDS), "$where": " AND ".join(where_parts)}
            all_rows.extend(client.get_all(SOURCE_A_DATASET_ID, params))
    else:
        # Capped: a single multi-day query filled entirely by whichever
        # date Socrata happens to return first would silently produce a
        # sample concentrated in one age bucket — useless for the lag
        # measurement this tool exists to produce (confirmed live: a
        # 300-cap over a 160-day range came back 300/300 from a single
        # day). Instead, spread the cap evenly across days, querying one
        # day at a time with its own small share of the total.
        rows_per_day = max(1, -(-limit // len(all_dates)))  # ceil division
        all_rows = []
        for d in all_dates:
            if len(all_rows) >= limit:
                break
            where_parts = [f"issue_date in ({build_in_clause([_mmddyyyy(d)])})"]
            if plates:
                where_parts.append(f"plate in ({build_in_clause(plates)})")
            params = {"$select": ",".join(SOURCE_A_FIELDS), "$where": " AND ".join(where_parts)}
            day_cap = min(rows_per_day, limit - len(all_rows))
            all_rows.extend(client.get_all(SOURCE_A_DATASET_ID, params, max_rows=day_cap))

    logger.info(
        "Fetched %d tickets from Source A (%s to %s)", len(all_rows), start_date, end_date
    )

    df = pd.DataFrame(all_rows, columns=SOURCE_A_FIELDS)
    df["summons_number"] = df["summons_number"].astype(str)
    df["issue_date"] = df["issue_date"].apply(parse_source_a_issue_date)

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

    No `$select` here, deliberately: historical FY dataset IDs don't
    necessarily share the current dataset's schema — confirmed live, the
    FY2023 ID has no `fiscal_year` column at all (that field was added in a
    later schema change per the spec's own data-quality warning), so a
    fixed field list would 400 against it. Fetching every column and
    reindexing onto SOURCE_B_FIELDS afterward means a genuinely absent
    column just comes back null instead of erroring the whole batch.
    """
    all_rows: list[dict] = []

    for dataset_id, summons_numbers in summons_numbers_by_dataset.items():
        for chunk in chunk_ids(summons_numbers, chunk_size=chunk_size):
            where_clause = f"summons_number in ({build_in_clause(chunk)})"
            params = {"$where": where_clause}
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
