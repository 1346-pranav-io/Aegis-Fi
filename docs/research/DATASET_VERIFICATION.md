# Dataset Verification Log

## IBM "Transactions for Anti Money Laundering (AML)", HI-Small — verified 2026-11-11
Status: **FILES VERIFIED BY DIRECT INSPECTION** (downloaded by the owner from [Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml); paper: [Altman et al., arXiv 2306.16424](https://arxiv.org/html/2306.16424v1)). Profiling script: `backend/scripts/profile_ibm_aml.py` (read-only). Licence: CDLA-Sharing-1.0 per the Kaggle listing; **licence text read 2026-11-11 from [cdla.dev](https://cdla.dev/sharing-1-0/)** (see 'Licence implications').

### Files (stored in `data/raw/ibm-aml/`, git-ignored)
| File | Bytes | SHA-256 |
|---|---|---|
| HI-Small_Trans.csv | 475,664,283 | `b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040` |
| HI-Small_accounts.csv | 34,053,187 | `786808526e33cfc441212dd6fccda7edfc24172149bed59c6ef59b186836b014` |
| HI-Small_Patterns.txt | 323,844 | `2c546b5ce6009e73851f0139af053cf845f08bf92f3bc82fe1eb937dec2ef39b` |

### Measured properties (Trans file)
- Header: `Timestamp, From Bank, Account, To Bank, Account, Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format, Is Laundering`. **Two columns are both named "Account"**; a naive dict reader silently overwrites one (handled with `header_override`).
- Rows: **5,078,345**; laundering (`Is Laundering=1`): **5,177 (0.1019%)**; distinct accounts (bank+account): **515,088**.
- **Time span: 2022/09/01 00:00 to 2022/09/18 16:18 (about 18 days).** Minute resolution, no seconds, no timezone (we interpret as UTC — an assumption).
- No transaction ID column. Our refs are `<file>:<row>`.
- 15 currencies given as names (e.g. "US Dollar", "Bitcoin"), not ISO codes. Payment formats: Reinvestment, Cheque, Credit Card, ACH, Cash, Wire, Bitcoin.
- Amount received differs from amount paid on 72,158 rows (cross-currency); self-transfers: 591,212; fully duplicate rows: 9.
### Accounts file
518,581 rows (more than the 515,088 accounts appearing in transactions); columns `Bank Name, Bank ID, Account Number, Entity ID, Entity Name`; 166,207 distinct entities. Entity names are synthetic ("Corporation #33520"); types: Partnership, Corporation, Sole Proprietorship, Country, Individual, Direct. Entity IDs make several accounts per entity possible, which is potentially useful for entity-resolution tests (to be examined).
### Patterns file and linking (measured 2026-11-11, `backend/scripts/link_patterns.py`)
- 370 BEGIN/END blocks, 3,209 transaction lines; types and transaction counts: FAN-OUT 48 blocks/342 txns, CYCLE 54/287, GATHER-SCATTER 51/716, STACK 43/466, RANDOM 41/191, BIPARTITE 49/263, FAN-IN 40/318, SCATTER-GATHER 44/626. Transactions per block: min 1, median 7, max 32.
- Pattern lines have the same 11 fields as Trans rows. **All 3,209 match exactly one Trans row each (0 unmatched, 0 ambiguous), no line is in two blocks, and all have Is Laundering = 1.**
- **Only 3,209 of the 5,177 laundering rows (62.0%) belong to any pattern block.** The other 1,968 laundering rows are in no block: ACH 1,275, Cheque 324, Credit Card 206, Cash 108, Bitcoin 55. **Why is not explained** by the dataset files or by the paper text I read (the generator paper describes the 8 patterns and calls the labels complete, but I found no statement about laundering transactions outside patterns). Do not assume a cause.
- Pattern names match the paper's pattern list (fan-out, fan-in, gather-scatter, scatter-gather, cycle, random, bipartite, stack).
### Entities (accounts file)
- 518,581 rows, all distinct (bank, account) pairs (account numbers alone repeat across banks, so the bank id is part of the key); no account has more than one entity. 3,493 accounts never appear in Trans; every account in Trans is in the accounts file.
- 63,518 entities own more than one account (max 7,820). Entity names are single synthetic strings ("Corporation #33520") with no name variants.
- The paper says the launderer owns or controls all accounts in a pattern. **In the data, none of the 370 blocks has all its accounts under one Entity ID** (2 to 45 distinct entities per block, median 8). So Entity ID cannot be used as ground truth for "same controlling party" in a laundering scheme.

### Daily volume and class balance (measured from the loaded DB, 2026-11-11)
Rows/positives per UTC day: Sep 1 1,114,921/322; Sep 2 754,449/408; Sep 3 207,382/391; Sep 4 207,430/407; Sep 5 482,650/471; Sep 6 482,089/531; Sep 7 482,751/497; Sep 8 482,773/539; Sep 9 654,467/514; Sep 10 208,325/442; then **Sep 11 396/232; Sep 12 281/170; Sep 13 184/106; Sep 14 121/70; Sep 15 46/28; Sep 16 46/26; Sep 17 23/15; Sep 18 11/8.** After Sep 10 almost all transactions are laundering (about 59% positive vs about 0.1% before). Cause unverified. Handled by D-24.

## Licence implications (CDLA-Sharing-1.0, read from cdla.dev; not legal advice)
- Using the data for computational analysis, and publishing Results (metrics, models, figures, with no more than a de minimis part of the data), carries no restriction (section 3.5). Our paper results are fine.
- If we Publish the Data or Enhanced Data (our database dumps, modified copies, derived datasets with substantial rows), it must go out under the unmodified CDLA-Sharing-1.0 with attribution preserved and notices that files were changed (section 3.1, 3.3). Our code licence cannot be applied to those files.
- Therefore: the reproducibility package should ship a download script plus hashes, not the data; keep `data/` git-ignored; do not commit sample rows beyond a tiny fixture. Seeded perturbation sets built from the data count as Enhanced Data if published.

## Implications for the research plan (for owner attention)
1. **Temporal robustness (RQ5) is constrained**: 18 days is a short window. Rolling-origin evaluation is possible only at day-level granularity and says little about long-term drift. Options: also use the Medium/Large variants (not downloaded, still same generator period unverified), or reframe RQ5 as short-horizon robustness. **Decided 2026-11-11 (owner): reframe RQ5 as short-horizon robustness (D-22).** Medium/Large variants not used for now.
2. **Investigation-level gold (METHODOLOGY §3.4)** is feasible for the 370 pattern blocks (typology + exact member transactions, linking verified). It does not exist for the 1,968 laundering transactions outside blocks (38%): cases built around them have only a binary label. Decision needed on how to treat them (see decisions.md D-23 proposal). Sizes are small: 40-54 blocks per pattern type; median 7 transactions per block.
3. Ground truth comes from a synthetic generator; results say nothing about real-world laundering prevalence or behaviour.

## Still to do
- Find out why 1,968 laundering transactions are in no pattern block (ask dataset authors / read the generator code if public).
- Check Medium/Large variants only if the 18-day span or the 370-block count proves limiting (not downloaded).
- Decide how seeded-perturbation ER tests will be constructed, given no name variants exist in the data.
