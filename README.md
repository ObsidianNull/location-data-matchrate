# location-data-matchrate

A script for comparing the [NYC Open Parking and Camera Violations](https://data.cityofnewyork.us/City-Government/Open-Parking-and-Camera-Violations/nc67-uf89) dataset against the [Parking Violations Issued](https://data.cityofnewyork.us/City-Government/Parking-Violations-Issued-Fiscal-Year-2024/pvqr-7yc4) dataset to measure what percentage of NYC traffic tickets have somewhat representative street-level location data.

## Background

The NYC Open Parking and Camera Violations dataset contains records of issued tickets but includes limited location information. The Parking Violations Issued dataset provides more granular street-level location data (house number, street name, intersecting street, etc.) for many of the same summonses. By matching records across both datasets using summons numbers, this script quantifies the **location data match rate** — the percentage of tickets for which richer location data is available.

## Datasets

| Dataset | Source | Description |
|---------|--------|-------------|
| Open Parking and Camera Violations | [NYC Open Data](https://data.cityofnewyork.us/City-Government/Open-Parking-and-Camera-Violations/nc67-uf89) | NYC traffic and camera tickets with payment/status information |
| Parking Violations Issued | [NYC Open Data](https://data.cityofnewyork.us/City-Government/Parking-Violations-Issued-Fiscal-Year-2024/pvqr-7yc4) | Detailed parking violation records including street-level location data |

## How It Works

1. Load records from both datasets (via CSV export or the Socrata API).
2. Join the two datasets on the shared **summons number** field.
3. Calculate the percentage of tickets from the violations dataset that have a matching record with location data in the parking violations dataset.
4. Report the overall match rate along with any relevant breakdowns (e.g., by year, violation type, or borough).

## Project Status

This project is currently being built from scratch. The script and supporting files will be added in future commits.

## License

This project is open source. Data sourced from [NYC Open Data](https://opendata.cityofnewyork.us/) is subject to the [NYC Open Data Terms of Use](https://www.nyc.gov/home/terms-of-use.page).
