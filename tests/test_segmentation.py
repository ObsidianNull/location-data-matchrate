from datetime import date

import pandas as pd
import pytest

from src.segmentation import (
    TYPE_CAMERA,
    TYPE_OFFICER,
    TYPE_UNKNOWN,
    add_segmentation_columns,
    assign_age_bucket,
    classify_ticket_type,
)


# ---------------------------------------------------------------------------
# classify_ticket_type
# ---------------------------------------------------------------------------


def test_dot_agency_is_camera():
    assert classify_ticket_type("DOT", "019") == TYPE_CAMERA


def test_precinct_000_is_camera_regardless_of_agency():
    assert classify_ticket_type("TRAFFIC", "000") == TYPE_CAMERA


@pytest.mark.parametrize("agency", ["TRAFFIC", "POLICE", "SANITATION"])
def test_known_officer_agencies(agency):
    assert classify_ticket_type(agency, "019") == TYPE_OFFICER


def test_lowercase_and_whitespace_agency_normalized():
    assert classify_ticket_type(" dot ", "019") == TYPE_CAMERA
    assert classify_ticket_type(" traffic ", "019") == TYPE_OFFICER


def test_unrecognized_agency_and_non_000_precinct_is_unknown():
    assert classify_ticket_type("PARKS", "019") == TYPE_UNKNOWN


def test_none_agency_and_none_precinct_is_unknown():
    assert classify_ticket_type(None, None) == TYPE_UNKNOWN


# ---------------------------------------------------------------------------
# assign_age_bucket
# ---------------------------------------------------------------------------


TODAY = date(2026, 8, 8)


def test_age_bucket_0_29_days():
    assert assign_age_bucket(date(2026, 7, 20), TODAY) == "0-30"  # 19 days


def test_age_bucket_boundary_30_days_owned_by_30_60():
    assert assign_age_bucket(date(2026, 7, 9), TODAY) == "30-60"  # exactly 30 days


def test_age_bucket_30_59_days():
    assert assign_age_bucket(date(2026, 6, 20), TODAY) == "30-60"


def test_age_bucket_boundary_60_days_owned_by_60_90():
    assert assign_age_bucket(date(2026, 6, 9), TODAY) == "60-90"  # exactly 60 days


def test_age_bucket_60_89_days():
    assert assign_age_bucket(date(2026, 5, 20), TODAY) == "60-90"


def test_age_bucket_boundary_90_days_owned_by_90_plus():
    assert assign_age_bucket(date(2026, 5, 10), TODAY) == "90+"  # exactly 90 days


def test_age_bucket_90_plus_days():
    assert assign_age_bucket(date(2026, 1, 1), TODAY) == "90+"


def test_age_bucket_zero_days_old():
    assert assign_age_bucket(TODAY, TODAY) == "0-30"


def test_age_bucket_accepts_iso_string():
    assert assign_age_bucket("2026-07-20T00:00:00.000", TODAY) == "0-30"


# ---------------------------------------------------------------------------
# add_segmentation_columns
# ---------------------------------------------------------------------------


def test_add_segmentation_columns():
    df = pd.DataFrame(
        [
            {"issuing_agency": "DOT", "precinct": "000", "issue_date": "2026-08-01"},
            {"issuing_agency": "TRAFFIC", "precinct": "019", "issue_date": "2026-01-01"},
        ]
    )

    result = add_segmentation_columns(df, TODAY)

    assert list(result["ticket_type"]) == [TYPE_CAMERA, TYPE_OFFICER]
    assert list(result["age_bucket"]) == ["0-30", "90+"]
