# Phase 7 hardening: measured bottlenecks (D-32)

Status: RESULTS · `experiments/p7_profile.py`, runs `p7_profile_20261112T200255Z` (before) and `p7_profile_20261112T200639Z` (after) · One run each on a laptop with other load: differences under about 20% are noise. 5.08M transactions, local Postgres in Docker. Numbers are indexed in `RESULTS_INDEX.json`.

## Measurements (before any change)
- Loading all transactions into a frame (`load_transactions`): 42.7 s. Of this, the SQL COPY to a temporary file is 20.7 s, CSV parsing 4.1 s, account factorisation 2.3 s; the remainder is pandas string and categorical work.
- Building the 23 point-in-time features: 19.5 s.
- API reads through the in-process client, 50 calls each: `GET /queue` p50 30.9 ms (limit 500) and 33.0 ms (limit 100), p95 under 50 ms. The development database has no cases, so `GET /cases/{id}` and `/audit` were **not** measured.

## One change made
`load_transactions` parsed the bank out of the account text for every row (10M string splits, 7.5 s in isolation). It now parses it once per distinct account (about 0.5M) and maps back. Checked on the full data: `origin_bank` and `dest_bank` codes are identical to the old method, element for element, so features and stored results are unaffected. After the change `load_transactions` takes 32.8 s (single run; the same-code SQL COPY step varied from 20.7 s to 17.7 s between the two runs, which is the noise level).

## Measured but not changed
- **SQL COPY (about 18-21 s)** is the largest single step. Speeding it up would mean an index or a materialised ordering, which is a schema change that needs a separate approved migration; not proposed, because analytics runs load once per experiment.
- **Feature build (18-19 s)** and **API reads (about 31 ms p50)** are not bottlenecks at this size.
- No profile exists for case detail, audit, or concurrent API load, for graph analytics beyond E2's figures, or for data larger than HI-Small. Do not read this as a scaling result.
