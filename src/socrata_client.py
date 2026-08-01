#!/usr/env/python3

# imports
import requests
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

# class definitions
class SocrataClient:
    # SocrataClient is a client for interacting with the Socrata API
    def __init__(self, domain: str, app_token: str):
        self.base_url = f"https://{domain}"
        self.session = requests.Session()
        self.session.headers.update({"X-App-Token": app_token})

    @retry(
        retry = retry_if_exception_type(TransientSocrataError),
        stop = stop_after_attempt(5),
        wait = wait_exponential(multiplier=1, min=1, max=30),
        before_sleep = before_sleep_log(logger, logging.WARNING),
        reraise = True,
    )


    def _request(self, dataset_id: str, params: dict) -> list[dict]:
        url = f"{self.base_url}/resource/{dataset_id}.json"

        try:
            response = self.session.get(url, params=params, timeout=10)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise TransientSocrataError(f"Network error calling {dataset_id}: {e}") from e
        
        if response.status_code == 429:
            raise TransientSocrataError(f"Rate limited (429) on {dataset_id}")
        if 500 <= response.status_code < 600:
            raise TransientSocrataError(f"Server error {response.status_code} on {dataset_id}"
            )
        if response.status_code == 400:
            raise NonTransientSocrataError(
                f"Bad request (400) on {dataset_id} - check $where clause: {response.text}"
            )
        if response.status_code == 404:
            raise NonTransientSocrataError(f"Dataset not found (404): {dataset_id} - may have been rotated"
            )
        
        response.raise_for_status()
        return response.json()
