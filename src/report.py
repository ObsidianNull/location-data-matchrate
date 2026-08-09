"""
Builds the two output artifacts: match_rate_raw.csv (one row per sampled
ticket, for spot-checking) and match_rate_summary.csv (the aggregate
numbers for the founder conversation).

Two flat CSVs rather than one .xlsx workbook: the spec left this choice
open. CSVs are simpler to generate correctly, trivial to unit-test exactly
(an .xlsx pivot table is not), and open fine in Excel/Sheets anyway for a
founder conversation — there's no real workbook-specific feature (macros,
live pivot refresh) this measurement tool needs.
"""

from pathlib import Path

import pandas as pd

from src.anomalies import (
    ANOMALY_COUNTY_MISMATCH,
    ANOMALY_FUTURE_ISSUE_DATE_B,
    ANOMALY_ISSUE_DATE_MISMATCH,
    ANOMALY_NULL_ISSUE_DATE_B,
    ANOMALY_PRECINCT_MISMATCH,
    fiscal_year_tag_summary,
)
from src.matching import (
    MATCH_UNMATCHED,
    TIER_COUNTY,
    TIER_HOUSE_NUMBER,
    TIER_INTERSECTING_STREET,
    TIER_NONE,
    TIER_PRECINCT,
    TIER_STREET_CODE,
)

RAW_COLUMNS = [
    "summons_number",
    "issue_date",
    "plate",
    "issuing_agency",
    "ticket_type",
    "precinct",
    "county",
    "age_bucket",
    "match_status",
    "precision_tier",
    "house_number",
    "street_name",
    "intersecting_street",
    "street_code1",
    "fy_tag_source_b",
    "issue_date_source_b",
    "precinct_mismatch_flag",
    "county_mismatch_flag",
    "issue_date_mismatch_flag",
    "anomaly_notes",
]

PRECISION_TIER_ORDER = [
    TIER_HOUSE_NUMBER,
    TIER_STREET_CODE,
    TIER_INTERSECTING_STREET,
    TIER_PRECINCT,
    TIER_COUNTY,
    TIER_NONE,
]

AGE_BUCKET_ORDER = ["0-30", "30-60", "60-90", "90+"]


def build_raw_report(df: pd.DataFrame) -> pd.DataFrame:
    """Select and rename the full pipeline's columns into the exact raw
    CSV schema from spec section 5a.
    """
    raw = df.rename(columns={"fiscal_year": "fy_tag_source_b"})
    return raw[RAW_COLUMNS].copy()


def _match_rate_pct(df: pd.DataFrame) -> float:
    if len(df) == 0:
        return 0.0
    matched = int((df["match_status"] != MATCH_UNMATCHED).sum())
    return round(matched / len(df) * 100, 2)


def overall_match_rate_table(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [{"metric": "overall_match_rate_pct", "value": _match_rate_pct(df), "sample_size": len(df)}]
    )


def match_rate_by_ticket_type(df: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "ticket_type": ticket_type,
            "match_rate_pct": _match_rate_pct(group),
            "sample_size": len(group),
        }
        for ticket_type, group in df.groupby("ticket_type")
    ]
    return pd.DataFrame(rows).sort_values("ticket_type").reset_index(drop=True)


def match_rate_by_age_bucket(df: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "age_bucket": bucket,
            "match_rate_pct": _match_rate_pct(df[df["age_bucket"] == bucket]),
            "sample_size": int((df["age_bucket"] == bucket).sum()),
        }
        for bucket in AGE_BUCKET_ORDER
    ]
    return pd.DataFrame(rows)


def precision_tier_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Distribution among matched tickets only, per spec 5b."""
    matched = df[df["match_status"] != MATCH_UNMATCHED]
    total = len(matched)
    counts = matched["precision_tier"].value_counts()
    rows = [
        {
            "precision_tier": tier,
            "count": int(counts.get(tier, 0)),
            "pct_of_matched": round(counts.get(tier, 0) / total * 100, 2) if total else 0.0,
        }
        for tier in PRECISION_TIER_ORDER
    ]
    return pd.DataFrame(rows)


def mismatch_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Precinct/county mismatch count and % among matched tickets, per
    spec 5b.
    """
    matched = df[df["match_status"] != MATCH_UNMATCHED]
    total = len(matched)
    rows = []
    for flag_col, label in [
        ("precinct_mismatch_flag", "precinct_mismatch"),
        ("county_mismatch_flag", "county_mismatch"),
    ]:
        count = int(matched[flag_col].sum())
        rows.append(
            {
                "mismatch_type": label,
                "count": count,
                "pct_of_matched": round(count / total * 100, 2) if total else 0.0,
            }
        )
    return pd.DataFrame(rows)


def anomaly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Count and % of each anomaly type, per spec 5b, out of the full
    sample (not just matched tickets) — an anomaly rate is most useful to
    the founder read against "how many tickets did we even look at."
    """
    total = len(df)
    rows = []
    for flag_col, label in [
        ("anomaly_null_issue_date_source_b", ANOMALY_NULL_ISSUE_DATE_B),
        ("issue_date_mismatch_flag", ANOMALY_ISSUE_DATE_MISMATCH),
        ("anomaly_future_issue_date_source_b", ANOMALY_FUTURE_ISSUE_DATE_B),
        ("precinct_mismatch_flag", ANOMALY_PRECINCT_MISMATCH),
        ("county_mismatch_flag", ANOMALY_COUNTY_MISMATCH),
    ]:
        count = int(df[flag_col].sum())
        rows.append(
            {
                "anomaly_type": label,
                "count": count,
                "pct_of_sample": round(count / total * 100, 2) if total else 0.0,
            }
        )
    return pd.DataFrame(rows)


def build_summary_sections(df: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """All summary tables from spec 5b, in the order they should appear in
    match_rate_summary.csv.
    """
    return [
        ("Overall Match Rate", overall_match_rate_table(df)),
        ("Match Rate by Ticket Type", match_rate_by_ticket_type(df)),
        ("Match Rate by Age Bucket", match_rate_by_age_bucket(df)),
        ("Precision Tier Distribution (among matched)", precision_tier_distribution(df)),
        ("Precinct/County Mismatches (among matched)", mismatch_summary(df)),
        ("Anomaly Counts (of full sample)", anomaly_summary(df)),
        ("Fiscal Year Tag Distribution (Source B)", fiscal_year_tag_summary(df)),
    ]


def write_raw_csv(df: pd.DataFrame, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_raw_report(df).to_csv(path, index=False)


def write_summary_csv(df: pd.DataFrame, path: Path) -> None:
    """Write every summary section into one CSV, each preceded by a
    '# Section Title' comment line — a single flat file a founder can open
    in any spreadsheet tool, with clearly labeled sections in place of
    separate sheets.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        for i, (title, section_df) in enumerate(build_summary_sections(df)):
            if i > 0:
                f.write("\n")
            f.write(f"# {title}\n")
            section_df.to_csv(f, index=False)


def write_reports(df: pd.DataFrame, raw_path: Path, summary_path: Path) -> None:
    write_raw_csv(df, raw_path)
    write_summary_csv(df, summary_path)
