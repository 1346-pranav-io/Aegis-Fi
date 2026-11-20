"""A deterministic stand-in for the model, used with `ScriptedProvider`.

It reads the rendered prompt (the `<data name="...">` blocks), and answers the three LLM tasks of
Phase 3 by rule. Faults are injected on purpose, chosen by a hash of (seed, claim text) - not by
a stateful RNG - so a replayed run makes identical choices. Every injected fault is recorded in
`ground_truth` (a side channel the pipeline never sees) so experiments can score detection.

THIS IS A HARNESS, NOT A MODEL. Numbers computed with it say how the pipeline treats the faults
the stub injects, and nothing about how Claude behaves.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from aegis.llm.types import ProviderRequest

_BLOCK = re.compile(r'<data name="(\w+)">\n(.*?)\n</data>', re.S)

FaultKind = Literal["WRONG_NUMBER", "FAKE_EVIDENCE_ID", "UNSUPPORTED_LEAP", "WRONG_ENTITY"]
ALL_FAULTS: tuple[FaultKind, ...] = (
    "WRONG_NUMBER", "FAKE_EVIDENCE_ID", "UNSUPPORTED_LEAP", "WRONG_ENTITY",
)  # fmt: skip


def data_blocks(user: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, body in _BLOCK.findall(user):
        body = body.replace("<\\/data", "</data")
        try:
            out[name] = json.loads(body)
        except json.JSONDecodeError:
            out[name] = body
    return out


def _unit(seed: int, key: str) -> float:
    h = hashlib.sha256(f"{seed}|{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _is_int_like(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _numeric_keys(content: dict[str, Any]) -> list[tuple[str, Any]]:
    out = []
    for k in sorted(content):
        v = content[k]
        if _is_int_like(v):
            out.append((k, v))
        elif isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v):
            out.append((k, v))
    return out


@dataclass
class StubConfig:
    seed: int = 0
    fault_rate: float = 0.0  # probability that a drafted claim is corrupted
    faults: tuple[FaultKind, ...] = ALL_FAULTS
    max_claims_per_evidence: int = 2
    verifier: Literal["faithful", "rubber_stamp", "reject_all"] = "faithful"
    reporter: Literal["faithful", "cite_nonverified", "no_citations", "inject_fact"] = "faithful"


@dataclass
class FixtureLLM:
    """Callable script for ScriptedProvider; routes by prompt id."""

    cfg: StubConfig = field(default_factory=StubConfig)
    ground_truth: dict[str, FaultKind | None] = field(default_factory=dict)  # claim text -> fault
    calls: dict[str, int] = field(default_factory=dict)

    def __call__(self, req: ProviderRequest) -> str | Exception:
        self.calls[req.prompt_id] = self.calls.get(req.prompt_id, 0) + 1
        blocks = data_blocks(req.user)
        handler: Callable[[dict[str, Any]], Any] = {
            "investigation.synthesise": self._investigate,
            "verification.entail": self._verify,
            "reporting.draft": self._report,
        }[req.prompt_id]
        return json.dumps(handler(blocks))

    # ------------------------------------------------------------------ investigator
    def _investigate(self, b: dict[str, Any]) -> dict[str, Any]:
        subject = b["case_summary"]["subject_ref"]
        claims: list[dict[str, Any]] = []
        for it in b["evidence_items"]:
            eid, content = it["evidence_id"], it["content"]
            for key, val in _numeric_keys(content)[: self.cfg.max_claims_per_evidence]:
                claims.append(self._numeric_claim(subject, eid, it["evidence_type"], key, val))
            if (
                _is_int_like(content.get("n_cycles_through_center"))
                and content["n_cycles_through_center"] > 0
            ):
                text = "The subject sits on a circular flow of funds"
                claims.append(self._claim(subject, eid, "n_cycles_through_center", None, text,
                                          "TYPOLOGY_HYPOTHESIS"))  # fmt: skip
        return {"claims": claims, "open_questions": []}

    def _numeric_claim(
        self, subject: str, eid: str, etype: str, key: str, val: Any
    ) -> dict[str, Any]:
        value = val if _is_int_like(val) else str(val)
        text = f"{key.replace('_', ' ')} is {val} ({etype})"
        fault = self._pick_fault(text)
        cited = [{"evidence_id": eid, "support_role": "SUPPORTS"}]
        pred, subj, kind = key, subject, "AGGREGATE"
        if fault == "WRONG_NUMBER":
            wrong = int(Decimal(str(val))) + 7
            value, text = wrong, f"{key.replace('_', ' ')} is {wrong} ({etype})"
        elif fault == "FAKE_EVIDENCE_ID":
            cited = [{"evidence_id": "ev_01HZZZZZZZZZZZZZZZZZZZZZZZ", "support_role": "SUPPORTS"}]
        elif fault == "UNSUPPORTED_LEAP":
            pred, value, kind = "is_money_mule", None, "INFERENCE"
            text = "The subject is acting as a money mule"
        elif fault == "WRONG_ENTITY":
            subj = "zzbank|NOSUCHACCOUNT"
        self.ground_truth[text] = fault
        return {
            "subject_ref": subj, "predicate": pred, "object_value": value, "unit": None,
            "time_scope": None, "text": text, "claim_kind": kind, "cited": cited,
            "confidence": "MEDIUM", "er_link_ids": [],
        }  # fmt: skip

    def _claim(
        self, subject: str, eid: str, pred: str, value: Any, text: str, kind: str
    ) -> dict[str, Any]:
        self.ground_truth[text] = None
        return {
            "subject_ref": subject, "predicate": pred, "object_value": value, "unit": None,
            "time_scope": None, "text": text, "claim_kind": kind,
            "cited": [{"evidence_id": eid, "support_role": "SUPPORTS"}],
            "confidence": "LOW", "er_link_ids": [],
        }  # fmt: skip

    def _pick_fault(self, text: str) -> FaultKind | None:
        if self.cfg.fault_rate <= 0 or not self.cfg.faults:
            return None
        if _unit(self.cfg.seed, "fault:" + text) >= self.cfg.fault_rate:
            return None
        i = int(_unit(self.cfg.seed, "kind:" + text) * len(self.cfg.faults))
        return self.cfg.faults[min(i, len(self.cfg.faults) - 1)]

    # -------------------------------------------------------------------- verifier
    def _verify(self, b: dict[str, Any]) -> dict[str, Any]:
        claim, evid = b["claim"], b["evidence"]
        st = claim["structure"]
        if self.cfg.verifier == "reject_all":
            return {"verdict": "NOT_ENTAILED", "quoted_span": "", "rationale": "stub rejects all"}
        if self.cfg.verifier == "rubber_stamp":
            span = str(evid[0]["summary_text"])[:12] if evid else ""
            return {"verdict": "ENTAILS", "quoted_span": span, "rationale": "stub accepts all"}
        pred, val = st["predicate"], st.get("object_value")
        found = [(k, v) for e in evid for k, v in _flat(e["content"]) if k == pred]
        if val is not None and found:
            for k, v in found:
                if _dec(v) is not None and _dec(val) is not None and _dec(v) == _dec(val):
                    return {"verdict": "ENTAILS", "quoted_span": f'"{k}": {_dump(v)}',
                            "rationale": f"evidence states {k}={v}"}  # fmt: skip
            k, v = found[0]
            return {"verdict": "CONTRADICTS", "quoted_span": f'"{k}": {_dump(v)}',
                    "rationale": f"evidence states {k}={v}, claim says {val}"}  # fmt: skip
        if val is None and found and any(v not in (0, "0", None, False) for _, v in found):
            k, v = found[0]
            return {"verdict": "ENTAILS", "quoted_span": f'"{k}": {_dump(v)}',
                    "rationale": f"{k} is non-zero in the evidence"}  # fmt: skip
        return {"verdict": "NOT_ENTAILED", "quoted_span": "",
                "rationale": "the evidence does not contain the claimed quantity"}  # fmt: skip

    # --------------------------------------------------------------------- reporter
    def _report(self, b: dict[str, Any]) -> dict[str, Any]:
        claims = b["claims"]
        items = [{"text": c["text"], "claim_ids": [c["claim_id"]]} for c in claims]
        mode = self.cfg.reporter
        if mode == "cite_nonverified":
            items.append({"text": "An unverified allegation.", "claim_ids": ["clm_NOT_VERIFIED"]})
        elif mode == "no_citations":
            items.append({"text": "The subject is clearly a launderer.", "claim_ids": []})
        elif mode == "inject_fact":
            items = [{"text": it["text"] + " Total of 999999.", "claim_ids": []} for it in items]
        return {"items": items}


def _flat(obj: Any, key: str = "") -> list[tuple[str, Any]]:
    if isinstance(obj, dict):
        return [kv for k, v in obj.items() for kv in _flat(v, str(k))]
    if isinstance(obj, list):
        return [kv for v in obj for kv in _flat(v, key)]
    return [(key, obj)]


def _dec(x: Any) -> Decimal | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        return Decimal(str(x))
    except InvalidOperation:
        return None


def _dump(v: Any) -> str:
    return json.dumps(v)
