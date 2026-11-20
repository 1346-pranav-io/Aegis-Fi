"""G1 grounding, V1 verification, O1 orchestration on seeded fixtures with the stub LLM.

Naming: EVALUATION_FRAMEWORK section 5 calls these E2/E3/E4, but docs/research/E2_GRAPH.md and
E3_BEHAVIOUR.md already use E2/E3 for different things (graph benchmark, behaviour check), so
these are G1-grounding, V1-verification and O1-orchestration.

Every number is a property of THIS pipeline and THIS stub LLM on tiny synthetic fixtures. None of
it measures Claude or real investigations. Counts are reported with Wilson 95% intervals; no
significance claims are made.
"""

from collections import Counter
from collections.abc import Callable
from typing import Any

from aegis.domain.agents import Budget
from aegis.eval.phase3.fixture_llm import ALL_FAULTS, StubConfig
from aegis.eval.phase3.fixtures import KINDS, FixtureCase, make_case
from aegis.eval.phase3.harness import (
    Run,
    fmt_rate,
    report_findings,
    run_case,
    stored_claim_status,
)
from aegis.runtime.policies import FixedPipeline, OrchestrationPolicy, RuleAdaptive
from aegis.runtime.state import RunStatus


def _cases(seeds: int) -> list[FixtureCase]:
    return [make_case(k, s) for k in KINDS for s in range(seeds)]


def _stub(base_seed: int, i: int, **kw: Any) -> StubConfig:
    return StubConfig(seed=base_seed * 1000 + i, **kw)


# ------------------------------------------------------------------------------------ G1
def g1_grounding(cfg: dict[str, Any]) -> dict[str, Any]:
    """B2 = every claim the drafting model produces is accepted as drafted.
    B3 = grounded: a claim is stored only if it cites evidence that exists in its context.
    Metric: unsupported-claim rate among accepted claims (a claim is 'unsupported' if the stub
    injected a fault into it - ground truth the pipeline never sees)."""
    cases = _cases(cfg["seeds_per_kind"])
    rows = []
    for rate in cfg["fault_rates"]:
        raw = faulty_raw = stored = faulty_stored = 0
        removed: Counter[str] = Counter()
        for i, case in enumerate(cases):
            run = run_case(case, FixedPipeline(), _stub(cfg["seed"], i, fault_rate=rate))
            truth = run.llm.ground_truth
            kept = set(stored_claim_status(run))
            raw += len(truth)
            faulty_raw += sum(1 for f in truth.values() if f)
            stored += len(kept)
            faulty_stored += sum(1 for t in kept if truth.get(t))
            for t, f in truth.items():
                if f and t not in kept:
                    removed[f] += 1
        rows.append({"fault_rate": rate, "b2": [faulty_raw, raw], "b3": [faulty_stored, stored],
                     "removed_by_grounding": dict(removed)})  # fmt: skip
    return {"n_cases": len(cases), "rows": rows}


# ------------------------------------------------------------------------------------ V1
VARIANTS: dict[str, tuple[StubConfig | None, bool]] = {
    # name -> (verifier mode override, use LLM verifier). None = no verification at all (B3).
    "B3 no verification": (None, True),
    "B4 deterministic only": (StubConfig(verifier="faithful"), False),
    "B4 deterministic + faithful verifier": (StubConfig(verifier="faithful"), True),
    "B4 deterministic + rubber-stamp verifier": (StubConfig(verifier="rubber_stamp"), True),
    "B4 deterministic + reject-all verifier": (StubConfig(verifier="reject_all"), True),
}


def v1_verification(cfg: dict[str, Any]) -> dict[str, Any]:
    cases = _cases(cfg["seeds_per_kind"])
    out = []
    for name, (vcfg, use_llm) in VARIANTS.items():
        leak = n_find = clean_rej = n_clean = faulty_total = caught = 0
        per_fault: dict[str, list[int]] = {f: [0, 0] for f in ALL_FAULTS if f != "FAKE_EVIDENCE_ID"}
        for i, case in enumerate(cases):
            stub = _stub(cfg["seed"], i, fault_rate=cfg["fault_rate"],
                         verifier=vcfg.verifier if vcfg else "faithful")  # fmt: skip
            run = run_case(case, FixedPipeline(), stub, use_llm_verifier=use_llm)
            truth = run.llm.ground_truth
            status = stored_claim_status(run)
            findings = set(status) if vcfg is None else report_findings(run)
            for t in status:
                fault = truth.get(t)
                if fault:
                    faulty_total += 1
                    per_fault[fault][1] += 1
                    if t not in findings:
                        caught += 1
                        per_fault[fault][0] += 1
                else:
                    n_clean += 1
                    clean_rej += t not in findings
            n_find += len(findings)
            leak += sum(1 for t in findings if truth.get(t))
        out.append({
            "variant": name, "unsupported_in_findings": [leak, n_find],
            "faulty_caught": [caught, faulty_total], "false_rejection": [clean_rej, n_clean],
            "caught_by_fault": {f: v for f, v in per_fault.items() if v[1]},
        })  # fmt: skip
    return {"n_cases": len(cases), "fault_rate": cfg["fault_rate"], "variants": out,
            "isolation_off": "NOT MEASURABLE with the stub: the stub verifier ignores context by "
                             "construction; this ablation needs a real model."}  # fmt: skip


# ------------------------------------------------------------------------------------ O1
def _required_found(case: FixtureCase, run: Run) -> bool:
    ev = run.backend.evidence
    for i in run.state.evidence_ids:
        d = ev.get_evidence(i).draft
        if d.evidence_type is not case.required:
            continue
        c = d.content
        if case.required.value == "GRAPH_CYCLES":
            return int(c.get("n_cycles_through_center", 0)) > 0
        if case.required.value == "GRAPH_FANIN_FANOUT":
            return int(c.get("center_out_distinct", 0)) >= 10
        if case.required.value == "TXN_STRUCTURING_CHECK":
            return int(c.get("n_just_below", 0)) >= 3
        if case.required.value == "BEHAV_SELF_DEVIATION":
            return c.get("status") == "OK"
    return False


def o1_orchestration(cfg: dict[str, Any]) -> dict[str, Any]:
    cases = _cases(cfg["seeds_per_kind"])
    policies: dict[str, Callable[[], OrchestrationPolicy]] = {
        "fixed_pipeline": FixedPipeline,
        "rule_adaptive": RuleAdaptive,
    }
    rows = []
    for name, make in policies.items():
        for max_steps in cfg["max_steps"]:
            steps = tokens = req = rep = full = 0
            statuses: Counter[str] = Counter()
            for i, case in enumerate(cases):
                run = run_case(
                    case, make(), _stub(cfg["seed"], i),
                    budget=Budget(max_steps=max_steps, max_seconds=600, max_tokens=10**7,
                                  max_cost_usd=100),
                )  # fmt: skip
                st = run.state
                steps += len(st.steps)
                tokens += st.tokens_used
                req += _required_found(case, run)
                rep += bool(st.report_ids)
                full += st.status is RunStatus.COMPLETE
                statuses[st.status.value] += 1
            n = len(cases)
            rows.append({"policy": name, "max_steps": max_steps, "n": n,
                         "mean_steps": steps / n, "mean_stub_tokens": tokens / n,
                         "required_evidence": [req, n], "report_produced": [rep, n],
                         "complete": [full, n], "statuses": dict(statuses)})  # fmt: skip
    return {"n_cases": len(cases), "rows": rows}


# ---------------------------------------------------------------------------------- render
def render_markdown(run_id: str, commit: str, cfg: dict[str, Any], res: dict[str, Any]) -> str:
    g1, v1, o1 = res["g1"], res["v1"], res["o1"]
    L = [
        "# Phase 3 fixture experiments: G1 grounding, V1 verification, O1 orchestration",
        "",
        f"Run id `{run_id}` · code commit `{commit}` · config `experiments/configs/"
        "p3_fixture_experiments.json`. Regenerate with "
        "`uv run python ../experiments/p3_fixture_experiments.py` from `backend/`.",
        "",
        "**What these numbers are.** Properties of this pipeline and a scripted STUB LLM on "
        f"{g1['n_cases']} tiny synthetic fixture cases (4 typologies x {cfg['seeds_per_kind']} "
        "seeds). Faults are injected on purpose by the stub; 'unsupported' means 'the stub "
        "injected a fault into this claim'. Nothing here measures Claude, real investigators, or "
        "real data, and no result may be quoted as evidence for or against LLM verification. "
        "Intervals are Wilson 95% on counts within one seeded set (cases are not independent "
        "samples of any population).",
        "",
        "**Naming.** EVALUATION_FRAMEWORK section 5 lists these as E2/E3/E4. "
        "`E2_GRAPH.md` and `E3_BEHAVIOUR.md` already use E2/E3 for other things, so they are "
        "named G1/V1/O1 here. MLflow is not used for these fixture runs (JSON + this file).",
        "",
        "## G1 grounding (B2 accept-as-drafted vs B3 citation/context-checked)",
        "",
        "| stub fault rate | B2 unsupported / accepted | B3 unsupported / stored "
        "| removed by grounding |",
        "|---|---|---|---|",
    ]
    for r in g1["rows"]:
        L.append(
            f"| {r['fault_rate']} | {fmt_rate(*r['b2'])} | {fmt_rate(*r['b3'])} | "
            f"{r['removed_by_grounding'] or '-'} |"
        )
    L += [
        "",
        "Reading: grounding only removes claims that cite evidence outside their context "
        "(FAKE_EVIDENCE_ID). Wrong numbers, wrong entities and unsupported leaps cite real "
        "evidence and pass this layer by design; catching them is verification's job (V1).",
        "",
        f"## V1 verification (fault rate {v1['fault_rate']})",
        "",
        "| variant | unsupported in findings | faulty claims kept out of findings "
        "| clean claims wrongly excluded |",
        "|---|---|---|---|",
    ]
    for v in v1["variants"]:
        L.append(
            f"| {v['variant']} | {fmt_rate(*v['unsupported_in_findings'])} | "
            f"{fmt_rate(*v['faulty_caught'])} | {fmt_rate(*v['false_rejection'])} |"
        )
    L += ["", "Faulty claims kept out of findings, by fault kind (stored claims only):", ""]
    for v in v1["variants"]:
        if v["variant"] != "B3 no verification":
            by = ", ".join(f"{f}: {fmt_rate(*c)}" for f, c in v["caught_by_fault"].items())
            L.append(f"- {v['variant']}: {by}")
    L += ["", f"Isolation-off ablation: {v1['isolation_off']}", "",
          "## O1 orchestration (fixed_pipeline vs rule_adaptive under a step budget)", "",
          "Quality target per case = the evidence type that explains it (cycle ring -> "
          "GRAPH_CYCLES with a cycle; fan-out -> GRAPH_FANIN_FANOUT with >=10 distinct "
          "destinations; structuring -> TXN_STRUCTURING_CHECK with >=3 hits; drift -> "
          "BEHAV_SELF_DEVIATION with status OK). Cost = agent steps (and stub-estimated tokens, "
          "characters/4).", "",
          "| policy | max steps | mean steps used | required evidence found | report produced "
          "| run COMPLETE |",
          "|---|---|---|---|---|---|"]  # fmt: skip
    for r in o1["rows"]:
        L.append(
            f"| {r['policy']} | {r['max_steps']} | {r['mean_steps']:.2f} | "
            f"{fmt_rate(*r['required_evidence'])} | {fmt_rate(*r['report_produced'])} | "
            f"{fmt_rate(*r['complete'])} |"
        )
    L += ["", "Run statuses per row are in the JSON result file.", ""]
    return "\n".join(L)
