"""Name normalisation and the declared, versioned similarity scorer (FR-ER, D-06).

The score is a *similarity* in [0, 1], not a probability: it is not calibrated and must never be
shown or stored as one. Scorer weights and the digit penalty are declared constants below;
changing any of them, or the normaliser, REQUIRES a new `SCORER_VERSION` because link rows
record the version they were produced under.

Standard library only (no new dependency).
"""

import re
import unicodedata

SCORER_VERSION = "name-sim-1.0.0"
NORMALISER_VERSION = "norm-1.0.0"

# Declared parameters (part of the version).
W_JARO_WINKLER = 0.4
W_BIGRAM_DICE = 0.4
W_TOKEN_OVERLAP = 0.2
DIGIT_MISMATCH_FACTOR = 0.85  # applied when the numeric tokens differ (near-collision guard)

_SYNONYMS = {
    "corp": "corporation", "co": "corporation",
    "inc": "incorporated",
    "ltd": "limited",
    "partners": "partnership", "prtnshp": "partnership",
    "prop": "proprietorship", "proprietor": "proprietorship",
}  # fmt: skip
_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


def normalise_name(raw: str) -> str:
    """NFKC, casefold, drop punctuation, collapse whitespace, canonicalise legal-form words,
    drop the 'no' of 'No. 123'. Token ORDER is preserved here; the scorer sorts tokens."""
    s = unicodedata.normalize("NFKC", raw).casefold()
    s = _PUNCT.sub(" ", s)
    toks = s.split()
    out: list[str] = []
    for i, t in enumerate(toks):
        if t == "no" and i + 1 < len(toks) and toks[i + 1].isdigit():
            continue
        out.append(_SYNONYMS.get(t, t))
    return " ".join(out)


def numeric_tokens(normalised: str) -> tuple[str, ...]:
    return tuple(sorted(t for t in normalised.split() if any(ch.isdigit() for ch in t)))


def _bigrams(s: str) -> list[str]:
    return [s[i : i + 2] for i in range(len(s) - 1)] if len(s) > 1 else [s]


def bigram_dice(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    ba, bb = _bigrams(a), _bigrams(b)
    pool = list(bb)
    hits = 0
    for g in ba:
        if g in pool:
            pool.remove(g)
            hits += 1
    return 2.0 * hits / (len(ba) + len(bb))


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(max(la, lb) // 2 - 1, 0)
    ma, mb = [False] * la, [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not mb[j] and b[j] == ca:
                ma[i] = mb[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = trans = 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                trans += 1
            k += 1
    m = float(matches)
    return (m / la + m / lb + (m - trans / 2) / m) / 3.0


def jaro_winkler(a: str, b: str, p: float = 0.1, max_prefix: int = 4) -> float:
    j = jaro(a, b)
    prefix = 0
    for ca, cb in zip(a, b, strict=False):
        if ca != cb or prefix == max_prefix:
            break
        prefix += 1
    return j + prefix * p * (1.0 - j)


def token_overlap(a: str, b: str) -> float:
    """Soft token overlap: each token of the shorter name is matched to its most similar token of
    the other (Jaro-Winkler), then averaged. Tolerates typos inside tokens and dropped tokens."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 1.0 if ta == tb else 0.0
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return sum(max(jaro_winkler(x, y) for y in large) for x in small) / len(small)


def name_similarity(a_normalised: str, b_normalised: str) -> float:
    """Similarity of two *normalised* names, in [0, 1]. Symmetric. Token order is ignored (tokens
    are sorted), so token swaps do not lower the score."""
    sa = " ".join(sorted(a_normalised.split()))
    sb = " ".join(sorted(b_normalised.split()))
    if sa == sb:
        return 1.0
    base = (
        W_JARO_WINKLER * jaro_winkler(sa, sb)
        + W_BIGRAM_DICE * bigram_dice(sa, sb)
        + W_TOKEN_OVERLAP * token_overlap(sa, sb)
    )
    if numeric_tokens(a_normalised) != numeric_tokens(b_normalised):
        base *= DIGIT_MISMATCH_FACTOR
    return max(0.0, min(1.0, base))
