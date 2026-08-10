"""
Ticket-type segmentation (officer vs. camera) and age-bucket assignment.
Both take "today" as an explicit parameter rather than calling
datetime.now() internally, so this stays deterministic and testable.
"""

from datetime import date, datetime

import pandas as pd

# Source A's issuing_agency values are the full agency names, not the short
# codes the spec used as shorthand (DOT/TRAFFIC/POLICE/SANITATION) —
# confirmed against the live nc67-uf89 dataset during the Phase 12 dry run,
# where "DOT" never appears at all and the real values are "DEPARTMENT OF
# TRANSPORTATION", "POLICE DEPARTMENT", "DEPARTMENT OF SANITATION". Both
# forms are matched here in case a differently-sourced feed ever uses the
# short codes.
CAMERA_AGENCIES = {"DOT", "DEPARTMENT OF TRANSPORTATION"}
OFFICER_AGENCIES = {
    "TRAFFIC",
    "POLICE",
    "POLICE DEPARTMENT",
    "SANITATION",
    "DEPARTMENT OF SANITATION",
}
CAMERA_PRECINCT = "000"

TYPE_CAMERA = "camera"
TYPE_OFFICER = "officer"
TYPE_UNKNOWN = "unknown"


def classify_ticket_type(issuing_agency, precinct) -> str:
    """Officer vs. camera classification per spec 3.4.

    A DOT-issued ticket, or any ticket recorded against precinct "000" (how
    camera enforcement shows up in Source A, since cameras aren't precinct-
    dispatched), counts as camera — whichever signal fires first. A
    recognized officer agency (TRAFFIC/POLICE/SANITATION, by whichever name
    the feed uses) is officer; anything neither signal recognizes — transit
    police, parks department, other agencies present in the real data — is
    reported as "unknown" rather than guessed at.
    """
    agency = ("" if pd.isna(issuing_agency) else str(issuing_agency)).strip().upper()
    precinct_str = ("" if pd.isna(precinct) else str(precinct)).strip()

    if agency in CAMERA_AGENCIES or precinct_str == CAMERA_PRECINCT:
        return TYPE_CAMERA
    if agency in OFFICER_AGENCIES:
        return TYPE_OFFICER
    return TYPE_UNKNOWN


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def assign_age_bucket(issue_date, today: date) -> str:
    """Age bucket per spec 3.5: 0-30 / 30-60 / 60-90 / 90+ days.

    Each bucket owns its lower boundary — exactly 30 days old falls in
    30-60, exactly 90 falls in 90+ — decided once here rather than left
    ambiguous downstream.
    """
    age_days = (today - _as_date(issue_date)).days

    if age_days < 30:
        return "0-30"
    if age_days < 60:
        return "30-60"
    if age_days < 90:
        return "60-90"
    return "90+"


def add_segmentation_columns(df: pd.DataFrame, today: date) -> pd.DataFrame:
    """Attach ticket_type and age_bucket columns to a DataFrame that has
    Source A's issuing_agency, precinct, and issue_date columns.
    """
    df = df.copy()
    df["ticket_type"] = [
        classify_ticket_type(agency, precinct)
        for agency, precinct in zip(df["issuing_agency"], df["precinct"])
    ]
    df["age_bucket"] = [assign_age_bucket(issue_date, today) for issue_date in df["issue_date"]]
    return df
