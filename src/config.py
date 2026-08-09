"""
Loads environment configuration (.env) and the fiscal-year dataset ID table
(config/fiscal_year_datasets.yaml), and exposes a lookup for turning a
fiscal year (or "current") into a Socrata dataset ID.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FISCAL_YEAR_CONFIG_PATH = PROJECT_ROOT / "config" / "fiscal_year_datasets.yaml"


class ConfigError(Exception):
    """Raised when required configuration is missing or malformed."""


def load_app_token() -> str:
    """Load SOCRATA_APP_TOKEN from .env.

    Fails loudly rather than letting the client silently run unauthenticated
    against Socrata's much stricter public rate limit.
    """
    load_dotenv()
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if not token:
        raise ConfigError(
            "SOCRATA_APP_TOKEN is not set. Register a token at dev.socrata.com "
            "and add it to .env."
        )
    return token


def load_fiscal_year_datasets(path: Path = FISCAL_YEAR_CONFIG_PATH) -> dict:
    """Load the fiscal-year -> dataset ID table from YAML."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)

    if not data or "current" not in data:
        raise ConfigError(f"{path} is missing a required 'current' dataset ID entry.")

    return data


def get_dataset_id(fiscal_year: str, datasets: dict | None = None) -> str:
    """Return the Socrata dataset ID for a fiscal year, or 'current' for the
    live issuance dataset.

    `datasets` is normally the dict returned by load_fiscal_year_datasets();
    accepting it as a parameter instead of reloading YAML on every call keeps
    this cheap to call in a per-ticket loop and easy to test in isolation.
    """
    if datasets is None:
        datasets = load_fiscal_year_datasets()

    if fiscal_year == "current":
        return datasets["current"]

    history = datasets.get("history", {})
    if fiscal_year not in history:
        known = ["current", *sorted(history.keys())]
        raise ConfigError(
            f"Unknown fiscal year {fiscal_year!r}. Known keys: {known}. "
            "These dataset IDs are known to shift over time (NYC has reused "
            "this same ID for different fiscal years before) — add it to "
            "config/fiscal_year_datasets.yaml if it's a real, verified dataset."
        )

    return history[fiscal_year]
