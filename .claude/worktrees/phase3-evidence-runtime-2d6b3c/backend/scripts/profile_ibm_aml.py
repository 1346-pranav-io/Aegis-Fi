"""Read-only profile of the IBM AML HI-Small files. Usage: python -I profile_ibm_aml.py <dir>"""

import csv
import re
import sys
from collections import Counter
from pathlib import Path


def main(d: Path) -> None:
    trans = d / "HI-Small_Trans.csv"
    n = 0
    laundering = 0
    tmin = tmax = None
    cur_recv: Counter[str] = Counter()
    fmt: Counter[str] = Counter()
    diff_amount = diff_cur = self_xfer = 0
    seen: Counter[tuple[str, ...]] = Counter()
    accts: set[str] = set()
    ts_len: Counter[int] = Counter()
    with trans.open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r)
        for row in r:
            n += 1
            ts = row[0]
            ts_len[len(ts)] += 1
            tmin = ts if tmin is None or ts < tmin else tmin
            tmax = ts if tmax is None or ts > tmax else tmax
            laundering += row[10] == "1"
            cur_recv[row[6]] += 1
            fmt[row[9]] += 1
            diff_amount += row[5] != row[7]
            diff_cur += row[6] != row[8]
            self_xfer += (row[1], row[2]) == (row[3], row[4])
            accts.add(row[1] + "|" + row[2])
            accts.add(row[3] + "|" + row[4])
            seen[tuple(row)] += 1
    dup_rows = sum(c - 1 for c in seen.values() if c > 1)
    print("header:", header)
    print("rows:", n, "laundering:", laundering, f"({laundering / n:.4%})")
    print("time range (string min/max):", tmin, tmax, "timestamp lengths:", dict(ts_len))
    print("distinct accounts (bank|acct):", len(accts))
    print("currencies:", len(cur_recv), cur_recv.most_common(20))
    print("formats:", dict(fmt))
    print("amount received != paid:", diff_amount, " currency differs:", diff_cur)
    print("self transfers:", self_xfer, " fully duplicate rows:", dup_rows)

    pat = (d / "HI-Small_Patterns.txt").read_text(encoding="utf-8").splitlines()
    begins = [x for x in pat if x.startswith("BEGIN")]
    kinds = Counter(
        re.sub(r":.*", "", x.replace("BEGIN LAUNDERING ATTEMPT - ", "")) for x in begins
    )
    ends = sum(x.startswith("END") for x in pat)
    print("pattern blocks:", len(begins), "END markers:", ends)
    print("pattern types:", dict(kinds))
    print("pattern txn lines:", sum(1 for x in pat if x and not x.startswith(("BEGIN", "END"))))

    a = d / "HI-Small_accounts.csv"
    with a.open(newline="", encoding="utf-8") as f:
        rr = csv.reader(f)
        ah = next(rr)
        rows = list(rr)
    print("accounts header:", ah, "rows:", len(rows))
    print(
        "distinct account numbers:",
        len({x[2] for x in rows}),
        "distinct entities:",
        len({x[3] for x in rows}),
    )
    print("entity name prefixes:", Counter(x[4].split(" #")[0] for x in rows).most_common(8))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
