"""Deterministic claim checks (EVIDENCE_MODEL 5, FR-VER-01). Pure functions over plain JSON-like
payloads, shared by the Investigation agent (pre-store diagnostics) and the Verification agent
(first line of defence). They live here, not in a capability, because capabilities may not import
each other.

A check never guesses: it answers PASS, FAIL or NA ("cannot be checked here"). `opposite=True`
on a FAIL means the evidence states a DIFFERENT value for the very quantity the claim names.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from aegis.domain.evidence import CheckKind, CheckOutcome, VerificationCheck

_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class CheckResult:
    check: VerificationCheck
    opposite: bool = False


def to_decimal(x: Any) -> Decimal | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def leaf_items(obj: Any, key: str = "") -> Iterable[tuple[str, Any]]:
    """(last key, scalar) for every scalar leaf of a JSON-like value."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield from leaf_items(v, str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from leaf_items(v, key)
    else:
        yield key, obj


def payload_text(payloads: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(
        [{"c": p.get("content"), "s": p.get("summary_text"),
          "i": [i.get("ref_id") for i in p.get("inputs", [])]} for p in payloads],
        sort_keys=True, default=str, ensure_ascii=False,
    ).casefold()  # fmt: skip


def equals_at_precision(claimed: Decimal, actual: Decimal) -> bool:
    """Equal when `actual` rounded to the claim's own number of decimals equals the claim."""
    exp = claimed.as_tuple().exponent
    places = -exp if isinstance(exp, int) and exp < 0 else 0
    q = Decimal(1).scaleb(-places)
    return actual.quantize(q, rounding=ROUND_HALF_UP) == claimed.quantize(q, rounding=ROUND_HALF_UP)


def _chk(cid: str, outcome: CheckOutcome, detail: str = "") -> VerificationCheck:
    return VerificationCheck(
        check_id=cid, kind=CheckKind.DETERMINISTIC, outcome=outcome, detail=detail[:500]
    )


def check_entity_present(subject_ref: str, payloads: Sequence[Mapping[str, Any]]) -> CheckResult:
    if not subject_ref.strip():
        return CheckResult(_chk("ENTITY_PRESENT_IN_EVIDENCE", CheckOutcome.NA, "no subject"))
    found = subject_ref.casefold() in payload_text(payloads)
    return CheckResult(
        _chk(
            "ENTITY_PRESENT_IN_EVIDENCE",
            CheckOutcome.PASS if found else CheckOutcome.FAIL,
            "" if found else "subject does not appear in the cited evidence",
        )
    )


def check_values(
    structure: Mapping[str, Any],
    text: str,
    claim_kind: str,
    payloads: Sequence[Mapping[str, Any]],
) -> CheckResult:
    """AMOUNT_MATCHES: the claimed value, and every number written in the claim text, must be
    present in the cited evidence (at the claim's own precision)."""
    leaves = [(k, v) for p in payloads for k, v in leaf_items(p.get("content"))]
    nums = [(k, d) for k, v in leaves if (d := to_decimal(v)) is not None]
    blob = payload_text(payloads)
    problems: list[str] = []
    opposite = False
    value = structure.get("object_value")
    predicate = str(structure.get("predicate", ""))
    cv = to_decimal(value)
    if cv is not None:
        same_key = [d for k, d in nums if k == predicate]
        if same_key:
            # the evidence names this very quantity: the claim must agree with it. Matching some
            # OTHER number in the evidence by coincidence is not support (found by experiment V1).
            if not any(equals_at_precision(cv, d) for d in same_key):
                opposite = True
                problems.append(f"evidence states {predicate}={same_key[0]}, claim says {value}")
        elif not any(equals_at_precision(cv, d) for _, d in nums):
            problems.append(f"value {value} not found in evidence")
    elif isinstance(value, str) and value.strip():
        literal = claim_kind in ("FACT_OBSERVATION", "AGGREGATE")
        if literal and value.casefold() not in blob:
            problems.append(f"value {value!r} not found in evidence")
    for m in _NUM.findall(text):
        d = to_decimal(m)
        if d is None or any(equals_at_precision(d, e) for _, e in nums) or m.casefold() in blob:
            continue
        problems.append(f"number {m} in claim text not found in evidence")
        break
    if problems:
        return CheckResult(_chk("AMOUNT_MATCHES", CheckOutcome.FAIL, "; ".join(problems)), opposite)
    anything = cv is not None or bool(_NUM.search(text))
    return CheckResult(_chk("AMOUNT_MATCHES", CheckOutcome.PASS if anything else CheckOutcome.NA))


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def check_date_in_window(
    time_scope: str | None, payloads: Sequence[Mapping[str, Any]]
) -> CheckResult:
    dates = [d for m in _DATE.findall(time_scope or "") if (d := _parse_date(m))]
    if not dates:
        return CheckResult(_chk("DATE_IN_WINDOW", CheckOutcome.NA, "no ISO date in time_scope"))
    starts: list[date] = []
    ends: list[date] = []
    for p in payloads:
        w = p.get("window") or [None, None]
        for raw, bucket in ((w[0], starts), (w[1], ends)):
            if raw:
                bucket.append(datetime.fromisoformat(str(raw)).astimezone(UTC).date())
    if not starts or not ends:
        return CheckResult(_chk("DATE_IN_WINDOW", CheckOutcome.NA, "evidence has no window"))
    lo, hi = min(starts), max(ends)
    bad = [d.isoformat() for d in dates if not (lo <= d <= hi)]
    return CheckResult(
        _chk(
            "DATE_IN_WINDOW",
            CheckOutcome.FAIL if bad else CheckOutcome.PASS,
            f"{bad} outside evidence window {lo}..{hi}" if bad else "",
        )
    )


def check_as_of(as_of: datetime, payloads: Sequence[Mapping[str, Any]]) -> CheckResult:
    late = [
        p.get("evidence_id")
        for p in payloads
        if p.get("as_of") and datetime.fromisoformat(str(p["as_of"])).astimezone(UTC) > as_of
    ]
    return CheckResult(
        _chk(
            "AS_OF_RESPECTED",
            CheckOutcome.FAIL if late else CheckOutcome.PASS,
            f"evidence after as_of: {late}" if late else "",
        )
    )


def check_roles(citations: Sequence[Mapping[str, Any]]) -> CheckResult:
    roles = {str(c.get("support_role")) for c in citations}
    if "CONTRADICTS" in roles:
        return CheckResult(
            _chk(
                "CITATION_ROLE_CONSISTENT", CheckOutcome.FAIL, "claim cites contradicting evidence"
            ),
            opposite=True,
        )
    if not roles & {"SUPPORTS", "PARTIALLY_SUPPORTS"}:
        return CheckResult(
            _chk("CITATION_ROLE_CONSISTENT", CheckOutcome.FAIL, "no supporting citation")
        )
    return CheckResult(_chk("CITATION_ROLE_CONSISTENT", CheckOutcome.PASS))


def span_in_evidence(span: str, payloads: Sequence[Mapping[str, Any]]) -> bool:
    """A quoted span must occur verbatim (whitespace-normalised, case-insensitive)."""
    norm = " ".join(span.split()).casefold()
    if len(norm) < 3:
        return False
    return norm in " ".join(payload_text(payloads).split()) or any(
        norm in " ".join(json.dumps(p.get("content"), default=str).split()).casefold()
        or norm in " ".join(str(p.get("summary_text", "")).split()).casefold()
        for p in payloads
    )


def numbers_in(text: str) -> list[Decimal]:
    return [d for m in _NUM.findall(text) if (d := to_decimal(m)) is not None]
