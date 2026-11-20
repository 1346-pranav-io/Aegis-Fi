"""Report export as Markdown (FR-RPT-04). JSON export is the stored body itself.

Pure formatting of a stored report: nothing is added, recomputed or reordered beyond layout. Every
finding line carries its evidence ids, the unsupported and partially supported claims sit in their
own labelled sections, and open contradictions and limitations are always printed. The placeholder
template notice (Q-04: the official FIU-IND format is not verified) is always printed. Nothing is
sent anywhere: export is a read.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>&~])")


def md(text: object) -> str:
    """Make untrusted text safe to place in a Markdown line: newlines and control characters become
    spaces (so it cannot start a heading, list or fence), and every Markdown/HTML special
    character is backslash-escaped (so it cannot form links, images, emphasis or tags)."""
    flat = " ".join(str(text).split())
    flat = "".join(ch if ch.isprintable() else " " for ch in flat)
    return _SPECIALS.sub(lambda m: chr(92) + m.group(1), flat)


def _ids(ids: Sequence[str]) -> str:
    return ", ".join(f"`{i}`" for i in ids) if ids else "_none_"


def report_to_markdown(report: Mapping[str, Any]) -> str:
    """`report` is a stored row: {report_id, case_id, version, body, body_hash, generator...}."""
    body: Mapping[str, Any] = report["body"]
    case = body["case"]
    fmt = body.get("report_format", {})
    out: list[str] = [
        f"# Investigation report - case `{case['case_id']}` (version {report['version']})",
        "",
        f"> **{md(fmt.get('template', 'TEMPLATE'))}**: {md(fmt.get('note', ''))}".rstrip(": "),
        "",
        f"- Subject: {md(case['subject_ref'])}",
        f"- Run: `{case['run_id']}`",
        f"- Data as of: {case['as_of']}",
        f"- Report id: `{report['report_id']}`, body hash `{report['body_hash']}`",
        "",
        "## Findings (verified claims)",
        "",
    ]
    findings = body["findings"]
    if findings["items"]:
        for it in findings["items"]:
            out.append(
                f"- {md(it['text'])} (claim `{it['claim_id']}`; "
                f"evidence {_ids(it['evidence_ids'])})"
            )
    else:
        out.append("_No claim was verified: this report contains no findings._")
    if findings.get("narrative"):
        out += ["", f"### Narrative ({findings.get('narrative_source', 'NONE')})", ""]
        for n in findings["narrative"]:
            out.append(
                f"- {md(n['text'])} (claims {_ids(n['claim_ids'])}; "
                f"evidence {_ids(n['evidence_ids'])})"
            )
    out += ["", "## Partially supported (NOT findings)", ""]
    out += _rows(body.get("partially_supported", []))
    out += ["", "## Unsupported, contradicted or unverifiable (NOT findings)", ""]
    out += _rows(body.get("not_findings", []))
    out += ["", "## Open contradictions", ""]
    contradictions = body.get("open_contradictions", [])
    if contradictions:
        for c in contradictions:
            out.append(
                f"- `{c.get('contradiction_id', '?')}` {c.get('type', '')} "
                f"({c.get('severity', '')}, {c.get('status', '')})"
            )
    else:
        out.append("_none recorded_")
    unconfirmed = body.get("unconfirmed_entity_link_dependencies", [])
    if unconfirmed:
        out += ["", "## Claims relying on unconfirmed entity links", ""]
        out += [f"- claim `{u['claim_id']}` relies on {_ids(u['link_ids'])}" for u in unconfirmed]
    out += ["", "## Limitations", ""]
    out += [f"- {md(x)}" for x in body.get("limitations", [])]
    cov = body.get("citation_coverage", {})
    out += [
        "",
        f"_Citation coverage: {cov.get('cited', 0)} of {cov.get('total', 0)} findings cited._",
        "",
    ]
    return "\n".join(out)


def _rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return ["_none_"]
    return [
        f"- [{md(r['status'])}] {md(r['text'])} (claim `{r['claim_id']}`; "
        f"evidence {_ids(r['evidence_ids'])})"
        for r in rows
    ]
