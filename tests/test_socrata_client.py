"""
Tests for SocrataClient: retry/backoff on `_request`, pagination via
`get_all`, and the two standalone query-building helpers.

Key pattern: we never make real HTTP calls or real sleeps. We patch
`session.get` to return canned responses (or raise canned exceptions), and
we patch tenacity's actual sleep so the retry-exhaustion test doesn't take
1+2+4+8 = 15 real seconds.
"""

import logging

import pytest
import requests
from unittest.mock import MagicMock, patch

from src.socrata_client import (
    SocrataClient,
    TransientSocrataError,
    NonTransientSocrataError,
    build_in_clause,
    chunk_ids,
)


@pytest.fixture
def client():
    return SocrataClient(domain="data.cityofnewyork.us", app_token="fake-token-123")


def make_response(status_code, json_data=None, text=""):
    """Build a MagicMock that behaves enough like a requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or []
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# _request: retry/backoff behavior
# ---------------------------------------------------------------------------


def test_retries_on_500_then_succeeds(client):
    bad_response = make_response(500)
    good_response = make_response(200, json_data=[{"summons_number": "1234567890"}])

    with patch.object(
        client.session, "get", side_effect=[bad_response, good_response]
    ) as mock_get, patch("tenacity.nap.time.sleep", return_value=None):

        result = client._request("pvqr-7yc4", {"$where": "summons_number='1234567890'"})

    assert result == [{"summons_number": "1234567890"}]
    assert mock_get.call_count == 2


def test_retries_on_connection_error_then_succeeds(client):
    good_response = make_response(200, json_data=[])

    with patch.object(
        client.session,
        "get",
        side_effect=[requests.exceptions.ConnectionError("boom"), good_response],
    ) as mock_get, patch("tenacity.nap.time.sleep", return_value=None):

        result = client._request("pvqr-7yc4", {})

    assert result == []
    assert mock_get.call_count == 2


def test_retries_on_timeout_then_succeeds(client):
    good_response = make_response(200, json_data=[])

    with patch.object(
        client.session,
        "get",
        side_effect=[requests.exceptions.Timeout("too slow"), good_response],
    ) as mock_get, patch("tenacity.nap.time.sleep", return_value=None):

        result = client._request("pvqr-7yc4", {})

    assert result == []
    assert mock_get.call_count == 2


def test_retries_on_429_then_succeeds(client):
    rate_limited = make_response(429)
    good_response = make_response(200, json_data=[])

    with patch.object(
        client.session, "get", side_effect=[rate_limited, good_response]
    ) as mock_get, patch("tenacity.nap.time.sleep", return_value=None):

        result = client._request("pvqr-7yc4", {})

    assert result == []
    assert mock_get.call_count == 2


def test_400_fails_immediately_no_retry(client):
    bad_request = make_response(400, text="malformed $where clause")

    with patch.object(client.session, "get", return_value=bad_request) as mock_get:
        with pytest.raises(NonTransientSocrataError, match="Bad request"):
            client._request("pvqr-7yc4", {"$where": "not valid soql"})

    assert mock_get.call_count == 1


def test_404_fails_immediately_no_retry(client):
    not_found = make_response(404)

    with patch.object(client.session, "get", return_value=not_found) as mock_get:
        with pytest.raises(NonTransientSocrataError, match="Dataset not found"):
            client._request("some-rotated-id", {})

    assert mock_get.call_count == 1


def test_exhausts_retries_and_raises_transient_error(client):
    always_fails = make_response(500)

    with patch.object(
        client.session, "get", return_value=always_fails
    ) as mock_get, patch("tenacity.nap.time.sleep", return_value=None):

        with pytest.raises(TransientSocrataError):
            client._request("pvqr-7yc4", {})

    assert mock_get.call_count == 5


def test_exhausts_retries_on_repeated_connection_errors(client):
    with patch.object(
        client.session,
        "get",
        side_effect=requests.exceptions.ConnectionError("still down"),
    ) as mock_get, patch("tenacity.nap.time.sleep", return_value=None):

        with pytest.raises(TransientSocrataError):
            client._request("pvqr-7yc4", {})

    assert mock_get.call_count == 5


def test_retry_emits_warning_log(client, caplog):
    bad_response = make_response(500)
    good_response = make_response(200, json_data=[])

    with patch.object(
        client.session, "get", side_effect=[bad_response, good_response]
    ), patch("tenacity.nap.time.sleep", return_value=None), caplog.at_level(
        logging.WARNING
    ):

        client._request("pvqr-7yc4", {})

    assert any(
        "Retrying" in record.message or "500" in record.message
        for record in caplog.records
    )


def test_request_passes_explicit_timeout(client):
    good_response = make_response(200, json_data=[])

    with patch.object(client.session, "get", return_value=good_response) as mock_get:
        client._request("pvqr-7yc4", {"$where": "x=1"})

    _, kwargs = mock_get.call_args
    assert kwargs.get("timeout") == 30


# ---------------------------------------------------------------------------
# get_all: client-owned pagination
# ---------------------------------------------------------------------------


def test_get_all_stops_on_short_page(client):
    """A single page shorter than page_size means there's nothing more."""
    page = [{"summons_number": str(i)} for i in range(3)]

    with patch.object(client, "_request", return_value=page) as mock_request:
        result = client.get_all("nc67-uf89", {"$where": "x=1"}, page_size=1000)

    assert result == page
    mock_request.assert_called_once()
    called_params = mock_request.call_args[0][1]
    assert called_params["$limit"] == 1000
    assert called_params["$offset"] == 0


def test_get_all_loops_across_multiple_full_pages(client):
    """Two full pages followed by a short page: all rows returned, loop ends."""
    page_size = 2
    page1 = [{"summons_number": "1"}, {"summons_number": "2"}]
    page2 = [{"summons_number": "3"}, {"summons_number": "4"}]
    page3 = [{"summons_number": "5"}]

    with patch.object(
        client, "_request", side_effect=[page1, page2, page3]
    ) as mock_request, patch("src.socrata_client.time.sleep", return_value=None):
        result = client.get_all("nc67-uf89", {}, page_size=page_size)

    assert result == page1 + page2 + page3
    assert mock_request.call_count == 3

    offsets = [call.args[1]["$offset"] for call in mock_request.call_args_list]
    assert offsets == [0, 2, 4]


def test_get_all_sleeps_between_pages_not_after_last(client):
    page_size = 2
    page1 = [{"summons_number": "1"}, {"summons_number": "2"}]
    page2 = [{"summons_number": "3"}]

    with patch.object(
        client, "_request", side_effect=[page1, page2]
    ), patch("src.socrata_client.time.sleep", return_value=None) as mock_sleep:
        client.get_all("nc67-uf89", {}, page_size=page_size)

    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_with(client.request_delay)


def test_get_all_empty_result(client):
    with patch.object(client, "_request", return_value=[]) as mock_request:
        result = client.get_all("nc67-uf89", {"$where": "x=1"})

    assert result == []
    mock_request.assert_called_once()


# ---------------------------------------------------------------------------
# build_in_clause
# ---------------------------------------------------------------------------


def test_build_in_clause_quotes_and_joins_values():
    clause = build_in_clause(["1234567890", "0987654321"])
    assert clause == "'1234567890','0987654321'"


def test_build_in_clause_preserves_leading_zeros():
    clause = build_in_clause(["0012345678"])
    assert "'0012345678'" in clause
    assert clause.count("'") == 2  # not silently stripped/reformatted


def test_build_in_clause_escapes_embedded_quotes():
    clause = build_in_clause(["ABC'123"])
    assert clause == "'ABC''123'"


def test_build_in_clause_empty_list_returns_empty_string():
    assert build_in_clause([]) == ""


def test_build_in_clause_single_value():
    assert build_in_clause(["1234567890"]) == "'1234567890'"


# ---------------------------------------------------------------------------
# chunk_ids
# ---------------------------------------------------------------------------


def test_chunk_ids_exact_multiple():
    ids = [str(i) for i in range(10)]
    chunks = chunk_ids(ids, chunk_size=5)
    assert chunks == [ids[0:5], ids[5:10]]


def test_chunk_ids_with_remainder():
    ids = [str(i) for i in range(7)]
    chunks = chunk_ids(ids, chunk_size=5)
    assert chunks == [ids[0:5], ids[5:7]]


def test_chunk_ids_chunk_size_one():
    ids = ["a", "b", "c"]
    chunks = chunk_ids(ids, chunk_size=1)
    assert chunks == [["a"], ["b"], ["c"]]


def test_chunk_ids_empty_input():
    assert chunk_ids([], chunk_size=500) == []


def test_chunk_ids_default_size_is_500():
    ids = [str(i) for i in range(1200)]
    chunks = chunk_ids(ids)
    assert [len(c) for c in chunks] == [500, 500, 200]
