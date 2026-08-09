# Matchrate Project — Development Plan

Ordered by dependency, not by "importance." Each phase produces something the
next phase needs. Tests are written *alongside* each module, not deferred to a
separate "testing phase" — a pure function without a test the same day you
write it is a debt you'll pay later with interest.

---

## Phase 0 — Scaffolding (do first, ~30 min)

- [x] Repo structure per the earlier layout (`src/`, `tests/`, `config/`, `data/`)
- [x] `.gitignore` (Python template + `.env` + `data/`)
- [x] `.env` with `SOCRATA_APP_TOKEN` (register the token at dev.socrata.com first)
- [x] `requirements.txt` (requests, tenacity, pandas, PyYAML, python-dotenv, pytest, ruff)
- [x] `venv` created, dependencies installed
- [x] `config/fiscal_year_datasets.yaml` stubbed with the known IDs from the spec:
      current (`pvqr-7yc4`), FY2025 (`m5vz-tzqv`), FY2023 (`869v-vr48`), FY2014 (`jt7v-77mi`)

**Exit condition:** `python -c "import requests, tenacity, pandas, yaml, dotenv"` runs clean.

---

## Phase 1 — `socrata_client.py` (done)

The foundation everything else calls. Nothing above this layer should ever
construct a raw URL or `requests` call directly.

- [x] `SocrataClient` class: holds `requests.Session`, base domain, app token
      (set once via `X-App-Token` header on the session)
- [x] Retry/backoff via `tenacity`, scoped to transient failures only
      (connection errors, timeouts, 429, 5xx) — 400/404 fail immediately, no retry
- [x] `IN (...)` clause builder as its own testable function — string values,
      correctly single-quoted, comma-joined
- [x] Chunking function: splits a list of IDs into batches of a configurable size
      (default 500), separate and testable independent of any HTTP call
- [x] Client-owned pagination (`get_all`): loops over `$limit`/`$offset` internally
- [x] Small configurable delay between batches (`request_delay`, default 0.2s)

**Tests written:** `tests/test_socrata_client.py` — retry-then-succeed on
500/ConnectionError/Timeout/429, immediate-fail-no-retry on 400/404,
retry-exhaustion (call count == 5), warning-on-retry, explicit `timeout=30`,
`IN` clause quoting/escaping/leading-zeros, chunking edge cases, and
pagination (multi-page loop, short-page termination, inter-page delay).

**Exit condition:** met against mocked responses; live-dataset exercise of
pagination/chunking against real data deferred to Phase 12 (real-data dry run).

---

## Phase 2 — `config.py` (done)

- [x] Load `.env` via `python-dotenv`
- [x] Load `config/fiscal_year_datasets.yaml` via `PyYAML`
- [x] `get_dataset_id(fiscal_year, datasets)` lookup ("current" or a known FY key)
- [x] Fails loudly (`ConfigError`) if `SOCRATA_APP_TOKEN` is missing

**Tests:** `tests/test_config.py` — known-year lookup, unknown-year raises
`ConfigError` rather than `None`, missing-token raises, real YAML file loads
correctly.

---

## Phase 3 — `sampling.py` (pulls from Source A) (done)

- [x] `fetch_source_a_sample()`: queries `nc67-uf89` by date range (+ optional
      plate list) for the fields listed in spec section 2
- [x] `summons_number` forced to `str` immediately on read
- [x] Returns a `pandas.DataFrame`

**Tests:** `tests/test_sampling.py` — leading zeros preserved through the str
cast, correct `$where` date-range and plate-filter construction, expected
column set.

---

## Phase 4 — Source B batch fetch (done, lives in `sampling.py`)

- [x] `group_summons_numbers_by_dataset()`: buckets sampled summons numbers by
      which Source B dataset ID applies, using a 365-day recency window and an
      NYC fiscal-year label (`nyc_fiscal_year_label`, July 1–June 30) for
      anything older, falling back to `None` (skip, logged) when no historical
      ID is on file for that FY
- [x] `fetch_source_b()`: chunks each dataset's summons-number list (Phase 1's
      chunker), calls `client.get_all` per chunk, tags each row with
      `source_b_dataset_id` (needed later by canary selection)
- [x] `merge_source_a_and_b()`: left join on `summons_number`, `issue_date`
      collision resolved via `issue_date` (Source A) / `issue_date_source_b`

**Tests:** multi-chunk/multi-dataset grouping, a summons number missing from
every chunk stays absent (not silently dropped) and shows up as a null after
the merge, recent-vs-historical FY dataset selection, unknown-FY tickets
excluded rather than crashing.

---

## Phase 5 — `matching.py` (done)

- [x] `classify_match()`: `unmatched` / `matched_no_address` /
      `matched_street_only` / `matched_house_level`
- [x] `assign_precision_tier()`: house_number → street_code →
      intersecting_street → precinct (Source A fallback) → county → none
- [x] `normalize_county()`: borough map per spec, unknown values passed through
      upper-cased rather than dropped
- [x] Decision: `None`, `""`, and whitespace-only strings are all treated as
      blank (`_is_blank()`), applied uniformly everywhere a "present?" check
      is needed

**Tests:** one test per classification branch and per tier, boundary/edge
cases (empty vs. None vs. whitespace), full `add_match_columns()` integration
including a real left-join NaN case (not just None).

---

## Phase 6 — `segmentation.py` (done)

- [x] `classify_ticket_type()`: DOT or precinct "000" → camera;
      TRAFFIC/POLICE/SANITATION → officer; anything else → "unknown"
      (reported, not silently guessed)
- [x] `assign_age_bucket()`: 0-30 / 30-60 / 60-90 / 90+, takes `today` as a
      parameter (never calls `datetime.now()` internally)
- [x] Decision: each bucket owns its lower boundary — exactly 30/60/90 days
      old falls into the *next* bucket up, tested explicitly

**Tests:** every boundary value, agency/precinct combinations for ticket
type, ISO-string issue_date input.

---

## Phase 7 — `anomalies.py` (done)

- [x] Cross-field consistency check: precinct/county mismatch after
      normalization, scoped to matched tickets only, blank-on-either-side
      treated as "can't verify" rather than a mismatch
- [x] Anomaly flags: null issue_date in B, issue_date mismatch A vs. B,
      future issue_date in B, precinct/county mismatch — each counted, not
      discarded, and combined into a semicolon-joined `anomaly_notes` column
- [x] `fiscal_year_tag_summary()`: distribution of Source B `fiscal_year`
      values + max issue_date per value (the direct FY-rollover-lag measurement)

**Tests:** each anomaly type triggers on a crafted bad row and stays silent
on a clean row; unmatched rows never fire any anomaly even with bad-looking
underlying data; counts aggregate correctly across a synthetic DataFrame.

---

## Phase 8 — `canary.py` (done)

- [x] SQLite schema: `canaries` (summons_number, dataset_id,
      last_known_match_status, stored_at) + `run_history` (run_id, timestamp,
      sample_size, overall_match_rate, summary_json)
- [x] `check_canaries()`: re-queries every stored canary against its recorded
      dataset ID *first*; raises `CanaryFailure` naming the summons number and
      dataset on the first miss
- [x] **Decision (recorded in code, not just here): a canary failure hard-stops
      the run.** `run.py` lets `CanaryFailure` propagate and exits non-zero
      rather than continuing — a rotated dataset ID means nothing else fetched
      that run can be trusted either.
- [x] `select_canary_candidates()` + `store_canaries()`: after a successful
      run, replace the stored set with up to 5 fresh known-matched summons
      numbers (and the dataset ID each was actually found in)

**Tests:** fresh DB creates schema correctly; healthy canary check passes and
queries each canary against its own dataset ID; a missing canary raises
`CanaryFailure`; store/replace round-trips correctly.

---

## Phase 9 — `report.py` (done)

- [x] `build_raw_report()` — exact column set/order from spec 5a
- [x] Summary aggregates (spec 5b): overall rate, by ticket-type, by
      age-bucket, precision-tier distribution (among matched), mismatch
      counts (among matched), anomaly counts (% of full sample), fiscal-year
      distribution + max issue_date
- [x] **Decision: two flat CSVs, not one `.xlsx` workbook.** CSVs are simpler
      to generate correctly and far easier to unit-test exactly than an
      openpyxl pivot table; they open fine in Excel/Sheets for the founder
      conversation with no real workbook-only feature needed. Summary sections
      are written into one `match_rate_summary.csv` with `# Section Title`
      header rows in place of separate sheets.

**Tests:** every summary number in `tests/test_report.py` is asserted against
a hand-calculated value on a small synthetic DataFrame; an end-to-end
`write_reports()` test confirms both files are created with the right shape.

---

## Phase 10 — `run.py` (orchestration) (done)

- [x] Load config → **check canaries first** (hard stop on failure) → sample
      Source A → batch-fetch Source B → merge → match → segment →
      anomaly-check → write reports → refresh canaries + record run history
- [x] CLI via `argparse`: `--start-date`/`--end-date` (required),
      `--sample-size`, `--chunk-size`, `--raw-output`, `--summary-output`,
      `--db-path`, `--skip-canary-check`
- [x] Logging at each stage (`logging`, INFO level) so a failed run shows
      where it failed; `CanaryFailure` → exit code 2, other exceptions → 1

**Exit condition:** `python run.py --help` runs clean and all modules import
without error. Full live run against real data is Phase 12.

---

## Phase 11 — CI (GitHub Actions) (done)

- [x] `.github/workflows/ci.yml`: install deps, `ruff check .`, `pytest -q`
      on push/PR
- [x] No scheduled pipeline-run workflow yet, per plan — holding off until
      Phase 12 is done and a full manual run's output has been eyeballed

---

## Phase 12 — Real-data dry run + refinement (done)

- [x] Ran against a real Source A sample spanning all four age buckets
      (2026-03-01 to 2026-08-08, sample-size 160, one query/day so the cap
      doesn't concentrate in a single date)
- [x] Ran a second, separate historical sample (2023-02-01 to 2023-02-10)
      to exercise the FY2023 historical-dataset path (`869v-vr48`)
- [x] Sanity-checked summary numbers against raw rows by hand (age-bucket
      match rates, precision-tier counts, issue_date alignment between A/B)
- [x] Ran a third time to confirm canary re-check works against a
      previously-stored canary set ("All 5 canaries checked out")

**Real bugs/mismatches this run caught, all fixed:**

- `add_anomaly_columns` silently dropped its flag columns on a 0-row
  DataFrame (`df.apply(axis=1)` never invokes the row function when there
  are no rows, so pandas can't infer the result columns) — crashed
  `report.py` on an empty sample. Now handled explicitly.
- Source A's `issue_date` is `text`, formatted MM/DD/YYYY — not the
  floating-timestamp column the spec assumed. The original ISO `between`
  query silently matched **zero rows** instead of erroring. Rewritten to
  query `issue_date in (...)` against MM/DD/YYYY-formatted dates, and every
  fetched value is normalized to ISO immediately so nothing downstream has
  to know about this.
- A capped `--sample-size` over a wide date range, queried as one big
  multi-day batch, came back **300/300 from a single date** — every age
  bucket but "90+" was empty, defeating the tool's core purpose. Fixed by
  querying one day at a time with an even per-day quota when a limit is set.
- Real `issuing_agency` values are full names (`DEPARTMENT OF
  TRANSPORTATION`, `POLICE DEPARTMENT`, `DEPARTMENT OF SANITATION`) — the
  spec's short codes (DOT/TRAFFIC/POLICE/SANITATION) mostly don't appear in
  the live data at all. `classify_ticket_type` now matches both forms.
- Real county/borough codes go well beyond the spec's base map — `ST`/`R`/
  `Rich` for Staten Island, `Q`/`Qns`/`QNS`/`QUEEN` for Queens, `Kings`/
  `BROOK` for Brooklyn, `Manha` for Manhattan all showed up live.
  `BOROUGH_MAP` expanded accordingly.
- Source B's `street_code1/2/3` use `"0"` as a "no code assigned"
  placeholder, not a real street code (confirmed: every live
  `street_code1=="0"` row with no house_number had a genuine
  `intersecting_street` instead) — `assign_precision_tier` was crediting
  these as `street_code` tier. Fixed to treat `"0"` as unset.
- The historical FY2023 dataset (`869v-vr48`) has **no `fiscal_year`
  column at all** — a fixed `$select` field list 400'd against it.
  `fetch_source_b` no longer sends `$select`; missing columns just come
  back null via DataFrame reindexing instead of erroring the batch.
- `get_all()` didn't actually bound a query when `max_rows`/`limit` was
  set narrower than the full result — it pulled everything and truncated
  client-side. Added real query-level bounding.

173 tests, `ruff check .` clean, three successful live runs producing a
real, interpretable result: match rate climbs from 0% (0-30 days old) to
~77% (60-90 days) to 100% (90+ days) in the recent sample — a genuine,
evidence-based measurement of Source B ingestion lag, which is exactly what
this tool was built to produce.

---

## Notes on ordering flexibility

Phases 5–7 (matching, segmentation, anomalies) don't depend on each other and
could be built in a different order if one interests you more, or reordered
to match how the founder conversation is prioritized (e.g., if ticket-type
segmentation is the number the founder cares about most, build Phase 6 before
Phase 5's precision tiers). Phases 0–4 and 8–10, however, are a strict
dependency chain — don't skip ahead on those.
