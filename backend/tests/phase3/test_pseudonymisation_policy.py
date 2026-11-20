"""D-29: names are registered centrally by the gateway, and LIVE calls are refused when they carry
free text the system cannot prove is masked. Live calls remain disabled: no provider exists."""

import pytest
from p3_llm import KEY, Out, config, ok_json, registry

from aegis.llm.gateway import LlmGateway
from aegis.llm.providers import ScriptedProvider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import InMemoryCallRecorder
from aegis.llm.types import CallStatus, FreeTextBlocked, GatewayMode, StructuredRequest

NAME = "Zaphod Beeblebrox Trading Ltd"


def build(
    mode: GatewayMode, **kw: object
) -> tuple[LlmGateway, ScriptedProvider, InMemoryCallRecorder]:
    prov = ScriptedProvider(lambda _r: ok_json())
    rec = InMemoryCallRecorder()
    gw = LlmGateway(
        config=config(), prompts=registry(), pseudonymiser=Pseudonymiser(KEY), mode=mode,
        provider=prov, recorder=rec, sleep=lambda _s: None, **kw,  # type: ignore[arg-type]
    )  # fmt: skip
    return gw, prov, rec


def req(**kw: object) -> StructuredRequest:
    base: dict[str, object] = dict(
        prompt_id="test.echo", role="investigation",
        variables={"records": [{"owner": NAME}], "question": "who owns it?"},
    )  # fmt: skip
    base.update(kw)
    return StructuredRequest(**base)  # type: ignore[arg-type]


def test_names_known_to_the_system_are_masked_even_if_the_agent_registers_nothing() -> None:
    gw, prov, _ = build(GatewayMode.RECORD, name_source=lambda: {"ORG": [NAME]})
    gw.generate_structured(req(), Out)  # `sensitive` is empty: the calling agent forgot
    sent = " ".join(r.user for r in prov.requests)
    assert NAME not in sent and "ORG_" in sent


def test_without_a_name_source_the_same_name_would_reach_the_provider() -> None:
    """The control case: this is the failure the central registration prevents."""
    gw, prov, _ = build(GatewayMode.RECORD)
    gw.generate_structured(req(), Out)
    assert NAME in " ".join(r.user for r in prov.requests)


def test_names_added_later_are_masked_from_the_next_call_on() -> None:
    names: dict[str, list[str]] = {"ORG": []}
    gw, prov, _ = build(GatewayMode.RECORD, name_source=lambda: names)
    gw.generate_structured(req(), Out)
    names["ORG"].append(NAME)
    gw.generate_structured(req(variables={"records": [{"owner": NAME}], "question": "again?"}), Out)
    assert NAME not in prov.requests[-1].user


def test_a_live_gateway_cannot_be_built_without_a_central_name_source() -> None:
    with pytest.raises(ValueError, match="name_source"):
        build(GatewayMode.LIVE)


def test_live_refuses_free_text_unless_explicitly_allowed() -> None:
    gw, prov, rec = build(GatewayMode.LIVE, name_source=lambda: {"ORG": [NAME]})
    with pytest.raises(FreeTextBlocked, match="free text"):
        gw.generate_structured(req(free_text=("question",)), Out)
    assert prov.requests == []  # nothing reached the provider
    assert [r.status for r in rec.records][-1] is CallStatus.FREE_TEXT_REFUSED
    # a request with no free text is fine, and the name is still masked
    gw.generate_structured(req(), Out)
    assert NAME not in prov.requests[-1].user
    allowed, prov2, _ = build(
        GatewayMode.LIVE, name_source=lambda: {"ORG": [NAME]}, allow_free_text_live=True
    )
    allowed.generate_structured(req(free_text=("question",)), Out)
    assert prov2.requests


def test_record_and_replay_modes_are_not_restricted_by_the_free_text_rule() -> None:
    gw, prov, _ = build(GatewayMode.RECORD)
    gw.generate_structured(req(free_text=("question",)), Out)
    assert prov.requests
