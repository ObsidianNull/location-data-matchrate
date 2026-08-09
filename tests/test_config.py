import pytest

from src import config


def test_load_app_token_returns_token_when_present(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "abc123")

    assert config.load_app_token() == "abc123"


def test_load_app_token_raises_when_missing(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("SOCRATA_APP_TOKEN", raising=False)

    with pytest.raises(config.ConfigError, match="SOCRATA_APP_TOKEN"):
        config.load_app_token()


def test_get_dataset_id_current():
    datasets = {"current": "pvqr-7yc4", "history": {"FY2025": "m5vz-tzqv"}}
    assert config.get_dataset_id("current", datasets) == "pvqr-7yc4"


def test_get_dataset_id_known_history_year():
    datasets = {"current": "pvqr-7yc4", "history": {"FY2025": "m5vz-tzqv"}}
    assert config.get_dataset_id("FY2025", datasets) == "m5vz-tzqv"


def test_get_dataset_id_unknown_year_raises_clear_error():
    datasets = {"current": "pvqr-7yc4", "history": {"FY2025": "m5vz-tzqv"}}

    with pytest.raises(config.ConfigError, match="FY1999"):
        config.get_dataset_id("FY1999", datasets)


def test_load_fiscal_year_datasets_reads_real_yaml():
    datasets = config.load_fiscal_year_datasets()
    assert datasets["current"] == "pvqr-7yc4"
    assert datasets["history"]["FY2025"] == "m5vz-tzqv"
    assert datasets["history"]["FY2023"] == "869v-vr48"
    assert datasets["history"]["FY2014"] == "jt7v-77mi"


def test_load_fiscal_year_datasets_missing_current_key_raises(tmp_path):
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("history:\n  FY2025: m5vz-tzqv\n")

    with pytest.raises(config.ConfigError, match="current"):
        config.load_fiscal_year_datasets(bad_yaml)
