# location-data-matchrate

A script for comparing the [NYC Open Parking and Camera Violations](https://data.cityofnewyork.us/City-Government/Open-Parking-and-Camera-Violations/nc67-uf89) dataset against the [Parking Violations Issued](https://data.cityofnewyork.us/City-Government/Parking-Violations-Issued-Fiscal-Year-2024/pvqr-7yc4) dataset to measure what percentage of NYC traffic tickets have somewhat representative street-level location data.

## Background

The NYC Open Parking and Camera Violations dataset contains records of issued tickets but includes limited location information. The Parking Violations Issued dataset provides more granular street-level location data (house number, street name, intersecting street, etc.) for many of the same summonses. By matching records across both datasets using summons numbers, this script quantifies the **location data match rate** — the percentage of tickets for which richer location data is available.

## Datasets

| Dataset | Source | Description |
| --- | --- | --- |
| Open Parking and Camera Violations | [NYC Open Data](https://data.cityofnewyork.us/City-Government/Open-Parking-and-Camera-Violations/nc67-uf89) | NYC traffic and camera tickets with payment/status information |
| Parking Violations Issued | [NYC Open Data](https://data.cityofnewyork.us/City-Government/Parking-Violations-Issued-Fiscal-Year-2024/pvqr-7yc4) | Detailed parking violation records including street-level location data |

## How It Works

1. Load records from both datasets (via CSV export or the Socrata API).
2. Join the two datasets on the shared **summons number** field.
3. Calculate the percentage of tickets from the violations dataset that have a matching record with location data in the parking violations dataset.
4. Report the overall match rate along with any relevant breakdowns (e.g., by year, violation type, or borough).

## Project Structure

```text
.
├── run.py                          # entrypoint: sample -> fetch -> join -> classify -> report
├── config/
│   └── fiscal_year_datasets.yaml   # fiscal year -> Socrata dataset ID lookup
├── src/
│   ├── socrata_client.py           # Socrata API client (retry/rate-limit handling)
│   ├── config.py                   # loads .env and the fiscal-year dataset table
│   ├── sampling.py                 # Source A sampling + Source B batch fetch/merge
│   ├── matching.py                 # match/unmatched classification
│   ├── segmentation.py             # ticket-type/age-bucket segmentation
│   ├── anomalies.py                # anomaly and cross-field consistency checks
│   ├── canary.py                   # canary record tracking to catch dataset ID rotation
│   └── report.py                   # writes match_rate_raw.csv / match_rate_summary.csv
├── tests/                          # pytest suite, one file per src module
└── data/                           # gitignored: CSV outputs + run.db (created at runtime)
```

## Setup

1. Create a virtual environment and install dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

2. Register a Socrata app token at [dev.socrata.com](https://dev.socrata.com) and add it to a `.env` file in the project root:

   ```text
   SOCRATA_APP_TOKEN=your_token_here
   ```

## Running

```powershell
.venv\Scripts\python.exe run.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

This samples Source A tickets in the given date range, matches them against Source B, and writes `data/match_rate_raw.csv` (per-ticket detail) and `data/match_rate_summary.csv` (aggregate match rate) — creating `data/run.db` to track run history and canary records along the way.

Useful flags:

| Flag | Default | Description |
| --- | --- | --- |
| `--sample-size` | none | Cap on the Source A sample size |
| `--chunk-size` | `500` | Source B batch size per API call |
| `--raw-output` | `data/match_rate_raw.csv` | Path for the per-ticket CSV |
| `--summary-output` | `data/match_rate_summary.csv` | Path for the summary CSV |
| `--db-path` | `data/run.db` | SQLite path for run history/canaries |
| `--skip-canary-check` | off | Skip verifying canary records from the previous run before starting |

Run `.venv\Scripts\python.exe run.py --help` for the full list.

## Testing

```powershell
.venv\Scripts\python.exe -m pytest
```

## License

This project is open source. Data sourced from [NYC Open Data](https://opendata.cityofnewyork.us/) is subject to the [NYC Open Data Terms of Use](https://www.nyc.gov/home/terms-of-use.page).
