"""Read-only: link HI-Small_Patterns.txt lines to HI-Small_Trans.csv rows and profile entities.

Usage: python -I link_patterns.py <dir with the three files>
Pattern lines have the same 11 fields as Trans rows, so linking is exact-tuple matching.
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main(d: Path) -> None:
    blocks: list[tuple[str, list[tuple[str, ...]]]] = []
    cur: list[tuple[str, ...]] | None = None
    kind = ""
    for line in (d / "HI-Small_Patterns.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("BEGIN"):
            kind = re.sub(r":.*", "", line.replace("BEGIN LAUNDERING ATTEMPT - ", ""))
            cur = []
        elif line.startswith("END"):
            if cur is None:
                raise ValueError("END without BEGIN")
            blocks.append((kind, cur))
            cur = None
        elif line.strip() and cur is not None:
            cur.append(tuple(next(csv.reader([line]))))
    plines = [row for _, rows in blocks for row in rows]
    pset = Counter(plines)
    print("blocks:", len(blocks), "pattern lines:", len(plines), "distinct:", len(pset))
    print("all pattern lines have 11 fields:", all(len(r) == 11 for r in plines))
    print("pattern lines with Is Laundering != 1:", sum(r[10] != "1" for r in plines))

    # scan trans: index rows matching pattern lines; collect laundering rows
    matched_rows: Counter[tuple[str, ...]] = Counter()
    laundering_total = 0
    laundering_in_pattern = 0
    laundering_not_in_pattern = Counter()
    with (d / "HI-Small_Trans.csv").open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            t = tuple(row)
            in_p = t in pset
            if in_p:
                matched_rows[t] += 1
            if row[10] == "1":
                laundering_total += 1
                if in_p:
                    laundering_in_pattern += 1
                else:
                    laundering_not_in_pattern[row[9]] += 1
    unmatched = [p for p in pset if p not in matched_rows]
    ambiguous = sum(1 for p in pset if matched_rows[p] > 1)
    print("pattern lines found in Trans:", len(pset) - len(unmatched), "unmatched:", len(unmatched))
    print("pattern lines matching >1 Trans row (ambiguous):", ambiguous)
    print(
        "laundering rows total:",
        laundering_total,
        "covered by patterns:",
        laundering_in_pattern,
        f"({laundering_in_pattern / laundering_total:.1%})",
    )
    print(
        "laundering rows NOT in any pattern, by payment format:",
        dict(laundering_not_in_pattern.most_common()),
    )
    print("pattern lines per type:", dict(Counter(k for k, rows in blocks for _ in rows)))
    sizes = sorted(len(rows) for _, rows in blocks)
    print("transactions per block min/median/max:", sizes[0], sizes[len(sizes) // 2], sizes[-1])
    overlap = Counter(p for p in plines)
    print("pattern lines appearing in >1 block:", sum(1 for c in overlap.values() if c > 1))

    # entity structure
    ent: dict[tuple[int, str], set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    n_rows = 0
    with (d / "HI-Small_accounts.csv").open(newline="", encoding="utf-8") as f:
        rr = csv.DictReader(f)
        for a in rr:
            n_rows += 1
            ent[(int(a["Bank ID"]), a["Account Number"])].add(a["Entity ID"])
            names[a["Entity ID"]] = a["Entity Name"]
    multi = sum(1 for v in ent.values() if len(v) > 1)
    print(
        "accounts file rows:",
        n_rows,
        "distinct (bank,acct):",
        len(ent),
        "accounts with >1 entity:",
        multi,
    )
    acct_per_entity = Counter(e for v in ent.values() for e in v)
    print(
        "entities with >1 account:",
        sum(1 for c in acct_per_entity.values() if c > 1),
        "max accounts per entity:",
        max(acct_per_entity.values()),
    )
    in_trans: set[tuple[int, str]] = set()
    with (d / "HI-Small_Trans.csv").open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            in_trans.add((int(row[1]), row[2]))
            in_trans.add((int(row[3]), row[4]))
    print(
        "accounts in accounts file but never in Trans:",
        len(set(ent) - in_trans),
        "| in Trans but missing from accounts file:",
        len(in_trans - set(ent)),
    )
    # does one entity own all accounts in a pattern block (paper: launderer controls all nodes)?
    ents_per_block = []
    for _, rows in blocks:
        es: set[str] = set()
        for row in rows:
            for key in ((int(row[1]), row[2]), (int(row[3]), row[4])):
                es |= ent.get(key, set())
        ents_per_block.append(len(es))
    print(
        "distinct entities per pattern block min/median/max:",
        min(ents_per_block),
        sorted(ents_per_block)[len(ents_per_block) // 2],
        max(ents_per_block),
    )
    print(
        "blocks whose accounts all belong to ONE entity:", sum(1 for e in ents_per_block if e == 1)
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
