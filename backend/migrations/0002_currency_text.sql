-- OWNER-APPROVED: retroactive (NOT approved in advance; applied before approval process existed; disclosed in decisions.md 2026-11-11)
-- Source datasets (e.g. IBM AML) give currency names ("US Dollar"), not ISO-4217 codes.
-- Store currency exactly as given; normalisation belongs in derived data, not source.
ALTER TABLE source.transaction ALTER COLUMN currency TYPE text;
