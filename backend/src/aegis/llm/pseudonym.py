"""Stable, reversible pseudonymisation (D-10, Q-02).

Direct identifiers (names, account numbers, addresses, emails) are replaced by tokens before text
leaves the process and restored locally afterwards. Tokens are an HMAC of (kind, value) under a
local key, so the same value maps to the same token in every call and every process that holds
the key - which is what makes record/replay keys stable. The clear-text mapping stays in memory
(or wherever the caller persists `export()`); it is never written to call records.

Detection is two-layered:
  1. registered values (exact, case-insensitive, whole-token) - the reliable layer: callers
     register the identifiers they know are in the data;
  2. regex defence in depth for shapes that look like identifiers (long digit runs, e-mail
     addresses, hex account ids such as the IBM '8000EBD30').
Limits (stated, not hidden): free-text names that were never registered are NOT detected, and the
regex layer can over-match (a 9-12 character hex word with a digit becomes an ACCT token).
Amounts are deliberately left in clear because semantic verification needs them.
"""

import hashlib
import hmac
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("ACCT", re.compile(r"\b\d{10,}\b")),
    ("ACCT", re.compile(r"\b(?=[0-9A-F]*\d)(?=[0-9A-F]*[A-F])[0-9A-F]{8,12}\b")),
)
_TOKEN = re.compile(r"\b(?:NAME|ACCT|ADDR|EMAIL|ORG|PERSON|ID)_[0-9A-F]{10}\b")


class Pseudonymiser:
    def __init__(self, key: bytes) -> None:
        if len(key) < 16:
            raise ValueError("pseudonymisation key must be at least 16 bytes")
        self._key = key
        self._fwd: dict[tuple[str, str], str] = {}
        self._rev: dict[str, str] = {}
        self._registered: list[tuple[str, str]] = []  # (kind, value), longest first

    def token(self, kind: str, value: str) -> str:
        fk = (kind, value.casefold())
        tok = self._fwd.get(fk)
        if tok is None:
            mac = hmac.new(self._key, f"{kind}\x00{value.casefold()}".encode(), hashlib.sha256)
            tok = f"{kind}_{mac.hexdigest()[:10].upper()}"
            self._fwd[fk] = tok
            self._rev.setdefault(tok, value)
        return tok

    def register(self, kind: str, value: str) -> str:
        if not value.strip():
            raise ValueError("cannot register an empty value")
        tok = self.token(kind, value)
        if (kind, value) not in self._registered:
            self._registered.append((kind, value))
            self._registered.sort(key=lambda kv: -len(kv[1]))
        return tok

    def register_all(self, sensitive: Mapping[str, Iterable[str]]) -> None:
        for kind, values in sensitive.items():
            for v in values:
                if v.strip():
                    self.register(kind, v)

    # ---------------------------------------------------------------- forward
    @staticmethod
    def _pattern(value: str) -> re.Pattern[str]:
        """Whole-token match. Short identifiers (< 4 chars, e.g. an account called "A") match
        case-sensitively so ordinary words ("a") are not rewritten; over-matching a capital letter
        is harmless (it round-trips), under-matching would leak."""
        flags = 0 if len(value) < 4 else re.IGNORECASE
        return re.compile(rf"(?<![\w]){re.escape(value)}(?![\w])", flags)

    def _scrub(self, text: str) -> str:
        for kind, value in self._registered:
            text = self._pattern(value).sub(self.token(kind, value), text)
        for kind, rx in _PATTERNS:
            text = rx.sub(self._by_match(kind), text)
        return text

    def _by_match(self, kind: str) -> Callable[[re.Match[str]], str]:
        def sub(m: re.Match[str]) -> str:
            return self.token(kind, m.group(0))

        return sub

    def apply(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self._scrub(obj)
        if isinstance(obj, Mapping):
            return {str(k): self.apply(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.apply(v) for v in obj]
        return obj

    def leaks(self, text: str) -> list[str]:
        """Registered clear values still present in `text` (used as a pre-send guard)."""
        return [v for _, v in self._registered if self._pattern(v).search(text)]

    # ---------------------------------------------------------------- reverse
    def restore(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return _TOKEN.sub(lambda m: self._rev.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, Mapping):
            return {str(k): self.restore(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.restore(v) for v in obj]
        return obj

    def export(self) -> dict[str, str]:
        """token -> clear value. Sensitive: keep local, never log."""
        return dict(self._rev)
