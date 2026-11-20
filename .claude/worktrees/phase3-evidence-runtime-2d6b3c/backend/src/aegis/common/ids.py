"""Prefixed ULID identifiers."""

from ulid import ULID

PREFIXES = {
    "case",
    "ev",
    "clm",
    "run",
    "step",
    "batch",
    "txn",
    "acct",
    "party",
    "lbl",
    "job",
    "sig",
    "alert",
    "apr",
    "apv",
    "ver",
    "ctr",
    "erl",
    "men",
    "ent",
}


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix}")
    return f"{prefix}_{ULID()}"
