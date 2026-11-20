"""Blocking: cheap candidate generation so the scorer never compares every pair.

Keys per normalised name: each alphabetic token's first four characters, each numeric token, and
every *single-digit deletion* of a numeric token (two numbers that differ in one digit, whether
by typo or because they are different entities, then share a block and get scored - leaving them
out would make near-collisions look easier than they are). Blocks larger than `max_block` are
ignored as uninformative (e.g. the word 'corporation' in a corpus of corporations).
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator

BLOCKING_VERSION = "block-1.0.0"
DEFAULT_MAX_BLOCK = 200


def blocking_keys(normalised: str) -> frozenset[str]:
    keys: set[str] = set()
    for tok in normalised.split():
        if any(ch.isdigit() for ch in tok):
            digits = "".join(ch for ch in tok if ch.isdigit())
            keys.add(f"n:{digits}")
            for i in range(len(digits)):
                keys.add(f"d:{digits[:i]}{digits[i + 1 :]}")
        elif len(tok) >= 3:
            keys.add(f"t:{tok[:4]}")
    return frozenset(keys)


class BlockIndex:
    """Inverted index key -> ids. Deterministic iteration (sorted)."""

    def __init__(self, max_block: int = DEFAULT_MAX_BLOCK) -> None:
        self._max = max_block
        self._by_key: dict[str, set[str]] = defaultdict(set)

    def add(self, item_id: str, normalised_name: str) -> None:
        for k in blocking_keys(normalised_name):
            self._by_key[k].add(item_id)

    def extend(self, items: Iterable[tuple[str, str]]) -> None:
        for item_id, name in items:
            self.add(item_id, name)

    def candidates(self, normalised_name: str) -> Iterator[str]:
        seen: set[str] = set()
        for k in sorted(blocking_keys(normalised_name)):
            block = self._by_key.get(k, ())
            if len(block) > self._max:
                continue
            seen.update(block)
        yield from sorted(seen)
