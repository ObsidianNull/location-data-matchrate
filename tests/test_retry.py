"""
Test for the retry logic in SocrataClient.
"""

import logging

import pytest
import requests
from unittest.mock import MagicMock, patch

from src.socrata_client import (
    SocrataClient,
    TransientSocrataError,
    NonTransientSocrataError,
)

@pytest.fixture
def client():
    return SocrataClient(domain="data.cityofnewyork.us", app_token="dummy_token")

def make_response(status_code, json_data=None, text=""):
    """Build a mock response object that behaves like a requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or []
    resp.text = text
    return resp
# ------------------------------------------------------------------------
# 1. Transient failure then success -> retries and eventually returns data
# -----------------------------------------------------------------------

def test_retries_on_500_then_succeeds(client):
    bad_response = make_response(500)
    good_response = make_response(200, json_data=[{"summons_number": "1234567890"}])

    with patch.object(client.session, "get", side_effect=[bad_response, good_response]) as mock_get, \
            patch("tenacity.nap.time.sleep", return_value=None): #skip real backoff delay
        
        result = client._request("pvqr-7yc4", {"$where": "summons_number='1234567890'"})

        assert result == [{"summons_number": "1234567890"}]
        assert mock_get.call_count == 2 # one failure, followed by one success

def test_retries_on_connection_error_then_succeeds(client):
    good_response = make_response(200, json_data=[])

    with patch.object(
        client.session,
        "get",
        side_effect=[requests.exceptions.ConnectionError("boom"), good_response],
    ) as mock_get, \
        patch("tenacity.nap.time.sleep", return_value=None):

        result = client._request("pvqr-7yc4", {})

    assert result == []
    assert mock_get.call_count == 2
        