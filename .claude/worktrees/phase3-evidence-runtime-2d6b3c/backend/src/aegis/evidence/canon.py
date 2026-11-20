"""Make analytics output hashable. `common.hashing` rejects floats (non-deterministic text), so
evidence content converts them to fixed-precision Decimals first. NaN/inf become None."""

import math
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

FLOAT_DIGITS = 9


def hashable(obj: Any) -> Any:
    """Recursively convert numpy/float values into JSON- and hash-safe values."""
    item = getattr(obj, "item", None)
    if callable(item) and not isinstance(obj, (dict, list, tuple, str)):
        obj = item()  # numpy scalar -> python scalar
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str, Decimal, datetime)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return Decimal(repr(round(obj, FLOAT_DIGITS)))
    if isinstance(obj, dict):
        return {str(k): hashable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        seq = sorted(obj, key=repr) if isinstance(obj, (set, frozenset)) else obj
        return [hashable(v) for v in seq]
    dump = getattr(obj, "model_dump", None)
    if callable(dump):  # pydantic model
        return hashable(dump(mode="python"))
    raise TypeError(f"cannot make {type(obj).__name__} hashable")


_ACCOUNT_REF = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]+\|[A-Za-z0-9_]+")


def account_refs(obj: Any) -> list[str]:
    """Source account references ('bank|account') found anywhere in a JSON-like value; used to
    register identifiers for pseudonymisation before text goes to the LLM gateway."""
    found: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, str):
            found.update(_ACCOUNT_REF.findall(o))
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return sorted(found)
