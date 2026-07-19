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
- [x] `requirements.txt` (requests, tenacity, pandas, PyYAML, python-dotenv, pytest, openpyxl, ruff)
- [ ] `venv` created, dependencies installed
- [x] Empty `config/fiscal_year_datasets.yaml` stubbed with the known IDs from the spec:
      current (`pvqr-7yc4`), FY2025 (`m5vz-tzqv`), FY2023 (`869v-vr48`), FY2014 (`jt7v-77mi`)

**Exit condition:** `python -c "import requests, tenacity, pandas, yaml, dotenv"` runs clean.

---

## Phase 1 — `socrata_client.py` (in progress)

The foundation everything else calls. Nothing above this layer should ever
construct a raw URL or `requests` call directly.

- [ ] `SocrataClient` class: holds `requests.Session`, base domain, app token
      (set once via `X-App-Token` header on the session)
- [ ] Retry/backoff via `tenacity`, scoped to transient failures only
      (connection errors, timeouts, 429, 5xx) — 400/404 fail immediately, no retry
- [ ] `IN (...)` clause builder as its own testable function — string values,
      correctly single-quoted, comma-joined (this is the trickiest part; write
      the test *before* the implementation)
- [ ] Chunking function: splits a list of IDs into batches of a configurable size
      (default ~500), separate and testable independent of any HTTP call
- [ ] Client-owned pagination: a method that returns/yields the *complete*
      result set for a query, looping over `$limit`/`$offset` internally
- [ ] Small delay between batches (configurable, even though app-token requests
      aren't currently throttled — treat "currently" as provisional)

**Tests to write now:**
- `IN` clause builder: leading zeros preserved, correct quoting, empty list edge case
- Chunking: exact multiples, remainders, chunk size of 1, empty input
- Retry logic: mock a 500 then success → succeeds; mock a 400 → no retry, raises immediately
- Pagination: mock two pages then a short page → all rows returned, loop terminates

**Exit condition:** you can call `client.get_all(dataset_id, where_clause)` against
both `nc67-uf89` and `pvqr-7yc4` in a scratch script and get real rows back,
including a case that exercises pagination (a query returning >1000 rows) and
a case that exercises batch chunking (a list of >1000 summons numbers).

---

## Phase 2 — `config.py`

Small, but unblocks Phase 3 and gives you a template for how every other
module will load configuration.

- [ ] Load `.env` via `python-dotenv`
- [ ] Load `config/fiscal_year_datasets.yaml` via `PyYAML`
- [ ] Expose a simple lookup: given a fiscal year (or "current"), return the
      dataset ID to query
- [ ] Fail loudly if `SOCRATA_APP_TOKEN` is missing — don't silently run unauthenticated

**Tests:** lookup returns correct ID for known years; missing/unknown year
raises a clear error rather than `None` silently propagating downstream.

---

## Phase 3 — `sampling.py` (pulls from Source A)

First real use of the client against live data.

- [ ] Function: given a date range (and/or list of plates), query `nc67-uf89`
      for the fields listed in spec section 2 (`summons_number`, `plate`,
      `state`, `issue_date`, `violation`, `precinct`, `county`,
      `issuing_agency`, `judgment_entry_date`)
- [ ] Enforce `summons_number` stays a `str` from the moment it's read —
      never let it pass through anything that could cast it to `int`
- [ ] Return as a `pandas.DataFrame` (this is the shape everything downstream expects)

**Tests:** with a mocked client response, verify types are right (`summons_number`
is `str` even if the raw JSON gave you something numeric-looking), date parsing
is correct, expected columns are present.

**Exit condition:** you can pull a real sample (e.g. last 30 days) and get a
DataFrame with sane values — this is your first checkpoint that the whole
plumbing works end to end for one dataset.

---

## Phase 4 — Source B batch fetch

Not a new file necessarily — could live in `sampling.py` or a `enrichment.py` —
but conceptually distinct from Phase 3: given summons numbers from Source A,
batch-query Source B.

- [ ] Take the sampled `summons_number` list, chunk it (Phase 1's chunker),
      call `client.get_all` per chunk against the correct dataset ID (Phase 2's
      config lookup, accounting for tickets old enough to need a historical FY ID)
- [ ] Collect fields from spec section 2's Source B table
- [ ] Merge results back into a single DataFrame keyed by `summons_number`

**Tests:** mocked multi-chunk response merges correctly; a summons number with
no match in any chunk ends up correctly absent/null rather than silently dropped.

**Exit condition:** one DataFrame, one row per sampled ticket, columns from
both sources, with nulls where Source B had no match.

---

## Phase 5 — `matching.py`

Pure logic, no I/O — should be some of the most heavily tested code in the project.

- [ ] Match classification (spec 3.2): `unmatched` / `matched_no_address` /
      `matched_street_only` / `matched_house_level`
- [ ] Precision tier assignment (spec 3.3)
- [ ] County normalization (`K`/`BK`/`NY`/`MN`/`QN`/`BX`/`Bronx` → one canonical form)

**Tests:** one test per classification branch, including edge cases (empty
string vs. null vs. whitespace — decide now whether `""` and `None` are
treated identically, and write that decision into a test so it can't drift).

---

## Phase 6 — `segmentation.py`

- [ ] Ticket-type classification (spec 3.4): officer vs. camera, per
      `issuing_agency` + `precinct == "000"` check
- [ ] Age-bucket assignment (spec 3.5): `0–30` / `30–60` / `60–90` / `90+`,
      computed relative to "today" at run time — take "today" as a parameter,
      don't call `datetime.now()` inside the function itself, so it stays testable

**Tests:** boundary values (exactly 30 days, exactly 90 days — decide which
bucket owns the boundary and test it explicitly); agency/precinct combos for
ticket-type.

---

## Phase 7 — `anomalies.py`

- [ ] Cross-field consistency check (spec 3.6): precinct/county mismatch after
      normalization
- [ ] Anomaly flags (spec 3.7): null issue_date in B, issue_date mismatch
      between A/B, future issue_date in B, precinct/county mismatch
- [ ] Fiscal-year tag monitoring (spec 3.8): distribution of `fiscal_year`
      values + max `issue_date` per value

**Tests:** each anomaly type triggers on a crafted bad row and stays silent on
a clean row; counts aggregate correctly across a small synthetic DataFrame.

---

## Phase 8 — `canary.py`

First piece touching persistent state (SQLite) — build and test this in
isolation from the rest of the pipeline.

- [ ] Schema: canary summons numbers + last-known match status; run-history
      table (run_id, timestamp, summary stats)
- [ ] On each run: re-check stored canaries first; if a previously-matching
      canary now misses, raise a clear signal *before* the rest of the run is trusted
- [ ] Decide now (you flagged this earlier as a product decision): does a
      canary failure hard-stop the script, or flag-and-continue? Write that
      decision down as a comment in the code, not just in your head.
- [ ] After a successful run, store 3–5 new canary summons numbers + write a
      row to run-history

**Tests:** fresh DB creates schema correctly; canary check on a healthy DB
passes; canary check with a rotated/missing summons number produces the
correct signal (exception, return code, or log — whatever you decided above).

---

## Phase 9 — `report.py`

- [ ] Build `match_rate_raw.csv` — one row per ticket, exact columns from spec 5a
- [ ] Build summary aggregates (spec 5b): overall rate, by ticket-type, by
      age-bucket, precision-tier distribution, mismatch counts, anomaly counts,
      fiscal-year distribution
- [ ] Output `match_rate_summary.csv`, or optionally one `.xlsx` workbook with
      raw + a pivot-table-backed summary sheet (your call, noted as optional
      earlier — decide based on whether this goes to the founder as a workbook
      or as separate files)

**Tests:** given a small synthetic DataFrame with known values, assert the
summary numbers come out exactly as hand-calculated — this is the module
where a silent off-by-one would be most embarrassing in front of the founder.

---

## Phase 10 — `run.py` (orchestration)

Wires everything above together. Should be short — if it's not, logic has
leaked into the entrypoint instead of living in its module.

- [ ] Load config → sample from Source A → batch-fetch Source B → match →
      segment → anomaly-check → canary-check → write reports
- [ ] Basic CLI args (date range, sample size, output paths) via `argparse`
      or just constants for v1 — don't over-build this before you need it
- [ ] Logging (even just `print` or basic `logging` module) at each stage so a
      failed run tells you *where* it failed

**Exit condition:** one command runs the whole pipeline end to end against
live data and produces both CSVs (or the workbook) without manual intervention.

---

## Phase 11 — CI (GitHub Actions)

Do this once Phase 1–9's tests exist — no point wiring CI before there's
anything for it to run.

- [ ] Workflow: install deps, run `ruff`, run `pytest` on push/PR
- [ ] Hold off on a *scheduled* run-the-pipeline workflow until Phase 10 is
      solid and you've eyeballed at least one full manual run's output

---

## Phase 12 — Real-data dry run + refinement

- [ ] Run against a real sample spanning multiple age buckets (needs some
      tickets >90 days old to exercise the historical-FY-ID path from Phase 4)
- [ ] Sanity-check the summary numbers by hand against a handful of raw rows
- [ ] Revisit anything the spec flagged as a live risk during this run —
      nulled `issue_date`, fiscal-year rollover behavior — since these are
      exactly the failure modes the script exists to catch, not edge cases to
      wave away if they show up

---

## Notes on ordering flexibility

Phases 5–7 (matching, segmentation, anomalies) don't depend on each other and
could be built in a different order if one interests you more, or reordered
to match how the founder conversation is prioritized (e.g., if ticket-type
segmentation is the number the founder cares about most, build Phase 6 before
Phase 5's precision tiers). Phases 0–4 and 8–10, however, are a strict
dependency chain — don't skip ahead on those.