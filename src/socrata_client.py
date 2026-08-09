"""
Thin HTTP client for the Socrata Open Data API (SODA).

Nothing above this module should ever construct a raw Socrata URL or call
`requests` directly — every query goes through `SocrataClient`.
"""

import logging
import time

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 1000
DEFAULT_CHUNK_SIZE = 500


class TransientSocrataError(Exception):
    """Worth retrying: network issues, timeouts, 429, 5xx."""


class NonTransientSocrataError(Exception):
    """Retrying won't help: bad query (400), missing/rotated dataset (404)."""


def build_in_clause(values: list[str]) -> str:
    """Build the body of a SoQL `IN (...)` clause from string values.

    Every value is single-quoted (required for text fields in SoQL, and
    `summons_number` must always be treated as text) and any embedded single
    quote is escaped by doubling it. An unquoted or wrongly-typed value
    doesn't raise an error from Socrata — it just silently matches nothing —
    so this is the one place in the client that most needs to be correct.

    Example: build_in_clause(["1234567890", "0987654321"])
             -> "'1234567890','0987654321'"
    """
    escaped = [value.replace("'", "''") for value in values]
    quoted = [f"'{value}'" for value in escaped]
    return ",".join(quoted)


def chunk_ids(ids: list[str], chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[list[str]]:
    """Split a list of IDs into chunks of at most chunk_size, preserving order."""
    return [ids[i : i + chunk_size] for i in range(0, len(ids), chunk_size)]


class SocrataClient:
    """Client for reading public Socrata datasets via an App Token.

    Holds one `requests.Session` for connection reuse across the hundreds of
    batch calls a single run can make, and sets the App Token once at init
    via the `X-App-Token` header rather than passing it per call.
    """

    def __init__(self, domain: str, app_token: str, request_delay: float = 0.2):
        self.base_url = f"https://{domain}"
        self.session = requests.Session()
        self.session.headers.update({"X-App-Token": app_token})
        self.request_delay = request_delay

    @retry(
        retry=retry_if_exception_type(TransientSocrataError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _request(self, dataset_id: str, params: dict) -> list[dict]:
        """Fetch one page/batch. Retries transient failures; lets
        non-transient failures (bad query, rotated dataset) propagate
        immediately so they aren't masked by a retry loop.
        """
        url = f"{self.base_url}/resource/{dataset_id}.json"

        try:
            response = self.session.get(url, params=params, timeout=30)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise TransientSocrataError(f"Network error calling {dataset_id}: {e}") from e

        if response.status_code == 429:
            raise TransientSocrataError(f"Rate limited (429) on {dataset_id}")
        if 500 <= response.status_code < 600:
            raise TransientSocrataError(f"Server error {response.status_code} on {dataset_id}")
        if response.status_code == 400:
            raise NonTransientSocrataError(
                f"Bad request (400) on {dataset_id} - check $where clause: {response.text}"
            )
        if response.status_code == 404:
            raise NonTransientSocrataError(
                f"Dataset not found (404): {dataset_id} - may have been rotated"
            )

        response.raise_for_status()
        return response.json()

    def get_all(
        self,
        dataset_id: str,
        params: dict | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict]:
        """Return the complete result set for a query.

        Loops over $limit/$offset internally until a page comes back shorter
        than page_size. Callers never think about offsets — this handles
        both an unbounded Source A date-range query and a Source B batch
        query that's normally under one page, uniformly.
        """
        base_params = dict(params or {})
        all_rows: list[dict] = []
        offset = 0

        while True:
            page_params = dict(base_params)
            page_params["$limit"] = page_size
            page_params["$offset"] = offset

            page = self._request(dataset_id, page_params)
            all_rows.extend(page)

            if len(page) < page_size:
                break

            offset += page_size
            if self.request_delay:
                time.sleep(self.request_delay)

        return all_rows
