"""Small seeded fixture cases with known structure (AML typologies). Synthetic and tiny: they
exist to exercise the pipeline, not to represent real laundering. Each case says which evidence
an investigator would NEED to explain it (`required`), which is the quality target for O1."""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aegis.domain.evidence import EvidenceType
from aegis.runtime.services import SourceTxn

AS_OF = datetime(2022, 9, 10, tzinfo=UTC)
SUBJECT = "bank1|ACCT_A"
KINDS = ("CYCLE", "FAN_OUT", "STRUCTURING", "DRIFT")


@dataclass(frozen=True)
class FixtureCase:
    kind: str
    seed: int
    txns: list[SourceTxn]
    reason_codes: tuple[str, ...]
    required: EvidenceType  # the evidence type that explains this case


class _Builder:
    def __init__(self, seed: int) -> None:
        self.rnd = random.Random(seed)  # noqa: S311 - seeded fixture data, not security
        self.txns: list[SourceTxn] = []

    def add(self, o: str, d: str, hours_ago: float, amount: int) -> None:
        i = len(self.txns) + 1
        t = AS_OF - timedelta(hours=hours_ago)
        self.txns.append(
            SourceTxn(
                txn_id=f"txn_{i}",
                content_hash=f"hash_txn_{i}",
                event_time=t,
                ingested_at=t + timedelta(minutes=1),
                origin_ref=o,
                dest_ref=d,
                amount=Decimal(amount),
                currency="USD",
            )  # fmt: skip
        )

    def noise(self, n: int) -> None:
        for _ in range(n):
            self.add(f"bank2|N{self.rnd.randint(1, 9)}", f"bank2|M{self.rnd.randint(1, 9)}",
                     self.rnd.uniform(1, 70), self.rnd.randint(20, 400))  # fmt: skip


def make_case(kind: str, seed: int) -> FixtureCase:
    b = _Builder(seed * 7919 + KINDS.index(kind))
    r = b.rnd
    other = [f"bank1|P{i}" for i in range(1, 13)]
    if kind == "CYCLE":
        ring = [SUBJECT, "bank1|B", "bank1|C"]
        for lap in range(2):
            for j in range(3):
                b.add(
                    ring[j],
                    ring[(j + 1) % 3],
                    30 - lap * 9 - j * 2 + r.random(),
                    1000 + r.randint(0, 50),
                )
        b.noise(10)
        return FixtureCase(kind, seed, b.txns, ("CYCLE",), EvidenceType.GRAPH_CYCLES)
    if kind == "FAN_OUT":
        b.add("bank1|SRC", SUBJECT, 6, 12000)
        for j, o in enumerate(other):
            b.add(SUBJECT, o, 5 - j * 0.2 + r.random() * 0.1, 900 + r.randint(0, 99))
        b.noise(10)
        return FixtureCase(kind, seed, b.txns, ("FAN_OUT",), EvidenceType.GRAPH_FANIN_FANOUT)
    if kind == "STRUCTURING":
        for j in range(6):
            b.add(SUBJECT, other[j], 8 - j * 0.7, 9500 + r.randint(0, 400))
        b.noise(10)
        return FixtureCase(
            kind, seed, b.txns, ("STRUCTURING", "HIGH_VELOCITY"), EvidenceType.TXN_STRUCTURING_CHECK
        )
    if kind == "DRIFT":
        for h in range(72, 280, 8):  # steady history with two regular counterparties
            b.add(
                SUBJECT, "bank1|REG1" if (h // 8) % 2 else "bank1|REG2", h, 300 + r.randint(0, 20)
            )
        for k in range(20):  # burst to new counterparties in the last 24h
            b.add(SUBJECT, f"bank1|NEW{k}", 1 + k * 0.9, 310 + r.randint(0, 20))
        b.noise(10)
        return FixtureCase(
            kind, seed, b.txns, ("BEHAVIOUR_DRIFT",), EvidenceType.BEHAV_SELF_DEVIATION
        )
    raise ValueError(kind)
