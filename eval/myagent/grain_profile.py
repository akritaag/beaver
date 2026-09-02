"""Profile per-question join fan-out ("grain") facts, gold-blind.

Output: dw_grain_facts.json, question id -> list of fact lines (dw and dw_real
questions live in the same file; their ids do not collide). Re-running for a
dataset merges, so profile dw_real dev and dw dev_sampled in turn.

For each question: group the hinted join pairs by table pair (composite keys
are measured together, not per column); for each join, measure rows vs
distinct key values on each side to see which table's rows get multiplied.
For each column in the column-mapping hint, classify it as an entity column
(identifier/name/code, or unique per row) or a numeric measure, and say what
the join path does to it: entity columns that get multiplied need
COUNT(DISTINCT); measures that get multiplied must be aggregated at their own
table's grain, not summed over the joined result.

Reads only schema + counts. Never touches gold SQL or answers.
Usage (from eval/myagent):  python grain_profile.py [--q_fn dev]
"""
import os
import re
import sys
import json
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agent_common import connect  # noqa: E402

ENTITY_RE = re.compile(r"(_KEY|_ID|_CODE|_NUMBER|_NAME|_TITLE|_NBR|_NO)$", re.I)
NUMERIC = {"int", "bigint", "smallint", "tinyint", "decimal", "double", "float", "numeric"}


def profile(records, db="dw"):
    conn = connect(db)
    cur = conn.cursor()
    out = {}
    cache = {}
    types = {}

    def coltype(table, col):
        k = (table.upper(), col.upper())
        if k not in types:
            cur.execute("SELECT DATA_TYPE FROM information_schema.columns WHERE table_schema=%s "
                        "AND UPPER(table_name)=%s AND UPPER(column_name)=%s", (db, k[0], k[1]))
            r = cur.fetchone()
            types[k] = (r[0].lower() if r else "")
        return types[k]

    def stats(table, cols):
        key = (table, tuple(cols))
        if key not in cache:
            try:
                expr = "`%s`" % cols[0] if len(cols) == 1 else "CONCAT_WS('|'," + ",".join(f"`{c}`" for c in cols) + ")"
                cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT {expr}) FROM `{table}`")
                n, d = cur.fetchone()
                cache[key] = (int(n), int(d))
            except Exception:
                cache[key] = None
        return cache[key]

    for r in records:
        lines = []
        # group join pairs by (tableA, tableB) -> composite key columns
        joins = defaultdict(lambda: ([], []))
        for pair in r.get("join_keys") or []:
            if len(pair) != 2 or "." not in pair[0] or "." not in pair[1]:
                continue
            (ta, ca), (tb, cb) = pair[0].split(".", 1), pair[1].split(".", 1)
            if ta == tb:
                continue
            k = (ta, tb) if ta < tb else (tb, ta)
            if k == (ta, tb):
                joins[k][0].append(ca); joins[k][1].append(cb)
            else:
                joins[k][0].append(cb); joins[k][1].append(ca)
        mult = defaultdict(float)
        for (ta, tb), (ca, cb) in joins.items():
            sa, sb = stats(ta, ca), stats(tb, cb)
            if not sa or not sb:
                continue
            rpa, rpb = sa[0] / max(1, sa[1]), sb[0] / max(1, sb[1])
            ka, kb = ".".join([ta] + ca) if len(ca) == 1 else f"{ta}.({','.join(ca)})", \
                     ".".join([tb] + cb) if len(cb) == 1 else f"{tb}.({','.join(cb)})"
            def side(name, rows, dist, rp):
                return (f"{name} is unique per row ({rows} rows)" if rp < 1.05
                        else f"{name} repeats ~{rp:.1f} rows per value ({rows} rows, {dist} values)")
            lines.append(f"- Join {ka} = {kb}: {side(ka, *sa, rpa)}; {side(kb, *sb, rpb)}.")
            if rpb > 1.05:
                mult[ta] = max(mult[ta], rpb)
                lines.append(f"  -> each {ta} row appears ~{rpb:.1f} times after this join.")
            if rpa > 1.05:
                mult[tb] = max(mult[tb], rpa)
                lines.append(f"  -> each {tb} row appears ~{rpa:.1f} times after this join.")
        for concept, cols in (r.get("column_mapping") or {}).items():
            for full in cols or []:
                if "." not in full:
                    continue
                t, c = full.split(".", 1)
                s = stats(t, [c])
                if not s:
                    continue
                rp = s[0] / max(1, s[1])
                is_measure = coltype(t, c) in NUMERIC and not ENTITY_RE.search(c)
                m = mult.get(t, 1.0)
                if is_measure:
                    if m > 1.05:
                        lines.append(f"- \"{concept}\" -> {full} is a numeric measure of {t}; the hinted joins multiply {t} rows ~x{m:.1f}, "
                                     f"so SUM/AVG over the joined result would count each {t} row ~{m:.1f} times. "
                                     f"Aggregate it at {t}'s own grain (aggregate before joining) unless the question wants it weighted.")
                    else:
                        lines.append(f"- \"{concept}\" -> {full} is a numeric measure of {t}; the hinted joins do not multiply {t} rows.")
                else:
                    own = "unique per row in its own table" if rp < 1.05 else f"already repeats ~{rp:.1f} rows per value in its own table"
                    if m > 1.05:
                        lines.append(f"- \"{concept}\" -> {full}: {own}; the hinted joins multiply {t} rows ~x{m:.1f}, "
                                     f"so a plain COUNT over the joined result counts duplicates. To count distinct {concept}, use COUNT(DISTINCT {full}).")
                    else:
                        lines.append(f"- \"{concept}\" -> {full}: {own}; the hinted joins do not multiply {t} rows.")
        out[r["id"]] = lines
    conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q_fn", default="dev")
    ap.add_argument("--dataset", default="dw_real")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    recs = json.load(open(os.path.join(here, "..", "..", "data", a.dataset, f"{a.q_fn}.json"), encoding="utf-8"))
    facts = profile(recs)
    path = os.path.join(here, "dw_grain_facts.json")
    merged = {}
    if os.path.exists(path):
        merged = json.load(open(path, encoding="utf-8"))
        merged = {k: (v.splitlines() if isinstance(v, str) else v) for k, v in merged.items()}
    merged.update(facts)
    json.dump(dict(sorted(merged.items())), open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"profiled {len(facts)} {a.dataset}/{a.q_fn} questions ({sum(1 for v in facts.values() if v)} with facts); file now holds {len(merged)} -> {path}")


if __name__ == "__main__":
    main()
