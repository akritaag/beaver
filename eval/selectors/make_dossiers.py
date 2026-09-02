"""Generate per-question failure dossiers for every candidate-1 miss of a run.

Each dossier: question, hints, gold SQL + executed shape/sample, each candidate
SQL + executed shape/sample, which candidate (if any) matched gold, and the
selector/judge/crossvote verdicts where available. Written to <run>/dossiers/.

Usage: python make_dossiers.py <run_dir>
"""
import sys
import json
from pathlib import Path
from _common import run_dir, candidates, dev_questions, CANDIDATE_SEP
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results

SEP = CANDIDATE_SEP

def shape(df, err, n=5):
    if err is not None:
        return f"ERROR: {err[:250]}"
    if df is None or df.empty:
        return "EMPTY (0 rows)"
    s = df.head(n).to_string(max_cols=10)
    return f"{df.shape[0]} rows x {df.shape[1]} cols\n{s[:900]}"

def main():
    run = run_dir(sys.argv[1])
    creds = get_mysql_credentials("dw_real")
    import pandas as pd
    dev = dev_questions()
    multi = {x["file"].replace(".sql", ""): x for x in
             json.load(open(run / "summary_ex_acc.json", encoding="utf-8"))["details"]}
    extras = {}
    for name in ("summary_judge.json", "summary_crossvote.json"):
        p = run / name
        if p.exists():
            extras[name] = {x["id"]: x for x in json.load(open(p, encoding="utf-8"))["details"]}

    out_dir = run / "dossiers"
    out_dir.mkdir(exist_ok=True)
    misses = []
    hist = {1: 0, 2: 0, 3: 0}
    for qid, m in multi.items():
        mc = m.get("matched_candidate")
        if mc:
            hist[mc] = hist.get(mc, 0) + 1
        if m.get("candidate1_match"):
            continue
        misses.append(qid)
        r = dev[qid]
        cands = [c.strip() for c in (run / "generated" / f"{qid}.sql").read_text(encoding="utf-8").split(SEP) if c.strip()]
        gold = (run / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip()
        gdf, gerr = execute_sql_with_timeout(gold, creds)

        lines = [f"# {qid}", "", f"**Question:** {r['question']}", "",
                 f"**Hinted tables:** {r.get('tables')}",
                 f"**Join keys hint:** {r.get('join_keys')}",
                 f"**Column mapping hint:** {json.dumps(r.get('column_mapping'), ensure_ascii=False)[:600]}",
                 f"**Domain knowledge:** {json.dumps(r.get('domain_knowledge'), ensure_ascii=False)[:400]}",
                 f"**Sub-questions:** {json.dumps(r.get('sub_questions'), ensure_ascii=False)[:600]}", "",
                 f"**Scorer verdict:** matched_candidate={mc}  message={m.get('message','')[:200]}"]
        for name, data in extras.items():
            e = data.get(qid, {})
            lines.append(f"**{name.replace('summary_','').replace('.json','')}:** " +
                         json.dumps({k: v for k, v in e.items() if k != 'id'})[:250])
        lines += ["", "## Gold SQL", "```sql", gold, "```",
                  f"**Gold result:** {shape(gdf, gerr)}", ""]
        for i, c in enumerate(cands):
            df, err = execute_sql_with_timeout(c, creds)
            ok = "MATCHES GOLD" if (mc == i + 1) else "does not match"
            lines += [f"## Candidate {i+1} ({ok})", "```sql", c, "```",
                      f"**Result:** {shape(df, err)}", ""]
        (out_dir / f"{qid}.md").write_text("\n".join(lines), encoding="utf-8")

    idx = [f"# Dossier index — {run.name}", "",
           f"candidate-1 misses: {len(misses)} / {len(multi)}",
           f"matched-candidate histogram (c1/c2/c3): {hist.get(1,0)}/{hist.get(2,0)}/{hist.get(3,0)}",
           ""] + [f"- [{q}]({q}.md)" for q in sorted(misses)]
    (out_dir / "INDEX.md").write_text("\n".join(idx), encoding="utf-8")
    print(f"wrote {len(misses)} dossiers to {out_dir}")
    print(f"matched histogram c1/c2/c3: {hist.get(1,0)}/{hist.get(2,0)}/{hist.get(3,0)}")

if __name__ == "__main__":
    main()
