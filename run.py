#!/usr/bin/env python3
"""
Entrypoint: pulls a sample from Source A, batch-fetches matching Source B
rows, joins/classifies/segments/anomaly-checks them, verifies canaries, and
writes match_rate_raw.csv + match_rate_summary.csv.
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from src.anomalies import add_anomaly_columns
from src.canary import (
    CanaryFailure,
    check_canaries,
    get_connection,
    record_run,
    select_canary_candidates,
    store_canaries,
)
from src.config import load_app_token, load_fiscal_year_datasets
from src.matching import MATCH_UNMATCHED, add_match_columns
from src.report import write_reports
from src.sampling import (
    fetch_source_a_sample,
    fetch_source_b,
    group_summons_numbers_by_dataset,
    merge_source_a_and_b,
)
from src.segmentation import add_segmentation_columns
from src.socrata_client import SocrataClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

SOCRATA_DOMAIN = "data.cityofnewyork.us"
DATA_DIR = Path(__file__).resolve().parent / "data"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure the Source A -> Source B location-data match rate."
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--sample-size", type=int, default=None, help="Cap on the Source A sample size"
    )
    parser.add_argument("--chunk-size", type=int, default=500, help="Source B batch size")
    parser.add_argument("--raw-output", type=Path, default=DATA_DIR / "match_rate_raw.csv")
    parser.add_argument("--summary-output", type=Path, default=DATA_DIR / "match_rate_summary.csv")
    parser.add_argument("--db-path", type=Path, default=DATA_DIR / "run.db")
    parser.add_argument(
        "--skip-canary-check",
        action="store_true",
        help="Skip pre-run canary verification (a no-op anyway on a first-ever run)",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    today = date.today()

    logger.info("Loading configuration")
    app_token = load_app_token()
    datasets = load_fiscal_year_datasets()
    client = SocrataClient(domain=SOCRATA_DOMAIN, app_token=app_token)
    conn = get_connection(args.db_path)

    if not args.skip_canary_check:
        logger.info("Checking canaries from the previous run")
        check_canaries(conn, client)  # raises CanaryFailure -> hard stop, see canary.py

    logger.info("Sampling Source A tickets from %s to %s", args.start_date, args.end_date)
    source_a_df = fetch_source_a_sample(
        client, args.start_date, args.end_date, limit=args.sample_size
    )
    logger.info("Sampled %d tickets from Source A", len(source_a_df))

    logger.info("Grouping summons numbers by Source B dataset ID")
    grouped = group_summons_numbers_by_dataset(source_a_df, today, datasets)

    logger.info("Batch-fetching Source B")
    source_b_df = fetch_source_b(client, grouped, chunk_size=args.chunk_size)
    matched_summons_numbers = set(source_b_df["summons_number"])
    logger.info("Source B returned %d matched rows", len(source_b_df))

    logger.info("Merging Source A and Source B")
    merged_df = merge_source_a_and_b(source_a_df, source_b_df)

    logger.info("Classifying matches")
    matched_df = add_match_columns(merged_df, matched_summons_numbers)

    logger.info("Segmenting by ticket type and age bucket")
    segmented_df = add_segmentation_columns(matched_df, today)

    logger.info("Running anomaly and cross-field consistency checks")
    final_df = add_anomaly_columns(segmented_df, today)

    logger.info("Writing reports to %s and %s", args.raw_output, args.summary_output)
    write_reports(final_df, args.raw_output, args.summary_output)

    logger.info("Refreshing canaries and recording run history")
    overall_match_rate = (
        (final_df["match_status"] != MATCH_UNMATCHED).sum() / len(final_df) * 100
        if len(final_df)
        else 0.0
    )
    summary_json = json.dumps(
        {
            "sample_size": len(final_df),
            "overall_match_rate_pct": round(overall_match_rate, 2),
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
        }
    )
    record_run(
        conn,
        sample_size=len(final_df),
        overall_match_rate=overall_match_rate,
        summary_json=summary_json,
    )

    new_canaries = select_canary_candidates(final_df)
    if new_canaries:
        store_canaries(conn, new_canaries)
    else:
        logger.warning(
            "No matched tickets in this run to use as new canaries — keeping the existing set."
        )

    conn.close()
    logger.info(
        "Run complete: %.2f%% overall match rate across %d tickets",
        overall_match_rate,
        len(final_df),
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except CanaryFailure:
        logger.error("Canary check failed — dataset ID may have rotated. Stopping run.")
        return 2
    except Exception:
        logger.exception("Run failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
