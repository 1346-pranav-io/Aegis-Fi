"""Contradiction capability against a FAKE ToolGateway (Phase 3 builds the real one)."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from aegis.capabilities.contradiction.capability import (
    PERMISSIONS,
    TASK_TYPE,
    ContradictionCapability,
    validate_explanation,
)
from aegis.common.errors import ExternalServiceError, PermissionDenied
from aegis.domain.agents import (
    AgentName,
    AgentTask,
    Budget,
    Capability,
    ExplanationProposer,
    Permission,
    ResultStatus,
)
from aegis.domain.evidence import ContradictionType
from aegis.eval.perturbation import ContradictionCorpus, generate_contradiction_corpus

AS_OF = datetime(2022, 9, 10, tzinfo=UTC)


class FakeGateway:
    """Serves a seeded corpus; records every tool name it was asked for."""

    def __init__(self, corpus: ContradictionCorpus, *, fail: Exception | None = None) -> None:
        self.corpus = corpus
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.fail = fail
        self.extra_evidence: list[dict[str, Any]] = []

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((tool, arguments))
        if self.fail:
            raise self.fail
        c = self.corpus
        if tool == "list_case_evidence":
            items = [{"evidence_id": i, **d.model_dump(mode="json")} for i, d in c.evidence.items()]
            return {"items": items + self.extra_evidence}
        if tool == "list_case_claims":
            items = []
            for i, d in c.claims.items():
                v = c.verifications.get(i)
                items.append(
                    {
                        "claim_id": i,
                        **d.model_dump(mode="json"),
                        "verification": None if v is None else v.model_dump(mode="json"),
                    }
                )
            return {"items": items}
        if tool == "list_case_er_links":
            return {
                "items": [
                    {
                        "link_id": link.link_id,
                        "mention_id": link.mention_id,
                        "entity_id": link.entity_id,
                        "method": "FUZZY_NAME",
                        "match_score": 0.9,
                        "status": link.status.value,
                        "scorer_version": "v",
                    }
                    for link in c.links.values()
                ]
            }
        raise PermissionDenied(f"tool {tool} is not allowed for the contradiction agent")


def task(case_id: str = "case_seed", *, tokens: int = 0, cost: float = 0.0) -> AgentTask:
    return AgentTask(
        task_id="t1",
        run_id="r1",
        case_id=case_id,
        agent=AgentName.CONTRADICTION,
        as_of=AS_OF,
        task_type=TASK_TYPE,
        budget=Budget(max_steps=5, max_seconds=30, max_tokens=tokens, max_cost_usd=cost),
    )


class StubProposer:
    """Stub ExplanationProposer: deterministic, never a real model."""

    def __init__(self, response: Mapping[str, Any] | None | Exception) -> None:
        self.response = response
        self.subjects: list[Mapping[str, Any]] = []

    def propose(self, subject: Mapping[str, Any]) -> Mapping[str, Any] | None:
        self.subjects.append(subject)
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is None:
            return None
        if self.response.get("use_allowed"):
            cited = list(subject["allowed_evidence_ids"][:1])
            return {"text": self.response["text"], "cited_evidence_ids": cited}
        return self.response


def test_satisfies_the_capability_protocol_with_the_specified_permissions() -> None:
    cap: Capability = ContradictionCapability()
    assert cap.name is AgentName.CONTRADICTION
    assert cap.permissions == {
        Permission.READ_EVIDENCE,
        Permission.WRITE_PROPOSAL,
        Permission.CALL_LLM,
    }
    assert (
        Permission.WRITE_CLAIM not in PERMISSIONS and Permission.REQUEST_APPROVAL not in PERMISSIONS
    )


def test_finds_seeded_contradictions_and_uses_read_tools_only() -> None:
    corpus = generate_contradiction_corpus(seed=21, n_per_type=6, n_decoys_per_type=6)
    gw = FakeGateway(corpus)
    res = ContradictionCapability().run(task(), gw)
    assert res.status is ResultStatus.OK and res.error is None
    assert {name for name, _ in gw.calls} == {
        "list_case_evidence",
        "list_case_claims",
        "list_case_er_links",
    }
    assert all(a["case_id"] == "case_seed" for _, a in gw.calls)
    found = {(d.type, frozenset(m.member_id for m in d.members)) for d in res.contradictions}
    assert {(s.type, s.members) for s in corpus.seeded} == found
    assert all(len(d.members) >= 2 for d in res.contradictions)
    assert res.usage.tool_calls == 3
    # an agent result carries drafts only: it has no way to express a status
    assert not hasattr(res.contradictions[0], "status")


def test_no_llm_without_budget_and_no_proposer_means_no_explanations() -> None:
    corpus = generate_contradiction_corpus(seed=21, n_per_type=2, n_decoys_per_type=0)
    p = StubProposer({"text": "x", "use_allowed": True})
    res = ContradictionCapability(proposer=p).run(task(tokens=0), FakeGateway(corpus))
    assert p.subjects == [] and all(d.explanation_proposal is None for d in res.contradictions)
    res2 = ContradictionCapability().run(task(tokens=1000, cost=1.0), FakeGateway(corpus))
    assert all(d.explanation_proposal is None for d in res2.contradictions)


def test_valid_explanation_is_attached_as_a_proposal_that_changes_nothing() -> None:
    corpus = generate_contradiction_corpus(seed=21, n_per_type=2, n_decoys_per_type=0)
    p: ExplanationProposer = StubProposer(
        {"text": " Different reporting periods. ", "use_allowed": True}
    )
    res = ContradictionCapability(proposer=p).run(task(tokens=1000, cost=1.0), FakeGateway(corpus))
    assert res.contradictions
    for d in res.contradictions:
        ex = d.explanation_proposal
        assert ex is not None
        assert ex["is_proposal"] is True and ex["effect_on_status"] == "NONE"
        assert ex["text"] == "Different reporting periods." and ex["cited_evidence_ids"]
    assert {d.type for d in res.contradictions} >= {ContradictionType.ATTRIBUTE_CONFLICT}


def test_unsupported_or_malformed_explanations_are_discarded() -> None:
    allowed = {"ev1", "ev2"}
    ok = {"text": "t", "cited_evidence_ids": ["ev1"]}
    assert validate_explanation(ok, allowed) is not None
    for bad in (
        None,
        {"text": "t"},  # cites nothing
        {"text": "t", "cited_evidence_ids": []},
        {"text": "t", "cited_evidence_ids": ["ev9"]},  # evidence not part of this finding
        {"text": "t", "cited_evidence_ids": ["ev1", "ev9"]},
        {"text": "  ", "cited_evidence_ids": ["ev1"]},
        {"text": "x" * 2001, "cited_evidence_ids": ["ev1"]},
        {"text": "t", "cited_evidence_ids": "ev1"},
    ):
        assert validate_explanation(bad, allowed) is None
    corpus = generate_contradiction_corpus(seed=21, n_per_type=2, n_decoys_per_type=0)
    cap = ContradictionCapability(
        proposer=StubProposer({"text": "t", "cited_evidence_ids": ["nope"]})
    )
    res = cap.run(task(tokens=10, cost=1.0), FakeGateway(corpus))
    assert res.contradictions and all(d.explanation_proposal is None for d in res.contradictions)


def test_a_failing_proposer_does_not_fail_detection() -> None:
    corpus = generate_contradiction_corpus(seed=21, n_per_type=2, n_decoys_per_type=0)
    cap = ContradictionCapability(proposer=StubProposer(ExternalServiceError("gateway down")))
    res = cap.run(task(tokens=10, cost=1.0), FakeGateway(corpus))
    assert res.status is ResultStatus.OK and res.contradictions


def test_future_and_foreign_evidence_is_ignored_by_as_of_and_case() -> None:
    corpus = generate_contradiction_corpus(seed=22, n_per_type=1, n_decoys_per_type=0)
    gw = FakeGateway(corpus)
    some = next(iter(corpus.evidence.values()))
    future = some.model_copy(update={"as_of": AS_OF + timedelta(days=1)})
    foreign = some.model_copy(update={"case_id": "other"})
    gw.extra_evidence = [
        {"evidence_id": "ev_future", **future.model_dump(mode="json")},
        {"evidence_id": "ev_foreign", **foreign.model_dump(mode="json")},
    ]
    res = ContradictionCapability().run(task(), gw)
    ids = {m.member_id for d in res.contradictions for m in d.members}
    assert "ev_future" not in ids and "ev_foreign" not in ids


def test_errors_become_typed_results_not_exceptions() -> None:
    corpus = generate_contradiction_corpus(seed=21, n_per_type=1, n_decoys_per_type=0)
    denied = ContradictionCapability().run(task(), FakeGateway(corpus, fail=PermissionDenied("no")))
    assert denied.status is ResultStatus.DENIED and denied.error is not None
    down = ContradictionCapability().run(
        task(), FakeGateway(corpus, fail=ExternalServiceError("db down"))
    )
    assert down.status is ResultStatus.FAILED and down.error and down.error.retryable
    gw = FakeGateway(corpus)
    gw.extra_evidence = [{"evidence_id": "ev_bad", "nonsense": True}]
    bad = ContradictionCapability().run(task(), gw)
    assert bad.status is ResultStatus.FAILED and bad.error and bad.error.code == "BAD_TOOL_DATA"
    wrong = task().model_copy(update={"task_type": "something_else"})
    res = ContradictionCapability().run(wrong, FakeGateway(corpus))
    assert res.status is ResultStatus.FAILED and res.error and res.error.code == "UNSUPPORTED_TASK"
