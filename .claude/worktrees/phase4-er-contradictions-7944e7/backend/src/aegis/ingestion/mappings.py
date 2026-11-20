"""Dataset-specific column mappings. Each is verified against the real file header."""

from aegis.ingestion.adapters import CsvMapping

# Verified 2026-11-11 against HI-Small_Trans.csv (sha256 b19d39f5...c5b040). The file has two
# columns both named "Account", so header_override gives them distinct names.
# amount/currency = what the origin PAID; what the destination RECEIVED is kept in attributes
# (they differ on ~72k of 5.08M rows, i.e. cross-currency transfers).
# Timestamps carry no timezone and no seconds in the source; they are interpreted as UTC.
IBM_AML_TRANS = CsvMapping(
    adapter_name="ibm_aml_trans",
    adapter_version="1",
    header_override=(
        "timestamp",
        "from_bank",
        "from_account",
        "to_bank",
        "to_account",
        "amount_received",
        "receiving_currency",
        "amount_paid",
        "payment_currency",
        "payment_format",
        "is_laundering",
    ),
    time_column="timestamp",
    time_format="%Y/%m/%d %H:%M",
    origin_columns=("from_bank", "from_account"),
    destination_columns=("to_bank", "to_account"),
    amount_column="amount_paid",
    currency_column="payment_currency",
    type_column="payment_format",
    label_column="is_laundering",
    extra_columns=("amount_received", "receiving_currency"),
    provenance="IBM-AML-HI-Small",
)
