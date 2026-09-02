"""Score the frozen selector policy against gold by re-executing the picked
candidate: cross-model confirmation where it fires (summary_crossvote.json),
otherwise the stored judge pick (summary_judge.json). Used to re-score the
stack after the database rebuild without new LLM calls.

Usage: python stack_score.py <run_dir> [judge_summary_name]
"""
import sys
import json
import pandas as pd
from _common import run_dir, candidates
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results

base = run_dir(sys.argv[1])
judge_name = sys.argv[2] if len(sys.argv) > 2 else "summary_judge.json"
creds = get_mysql_credentials("dw_real")
cross = {x["id"]: x for x in json.load(open(base / "summary_crossvote.json", encoding="utf-8"))["details"]}
judge = {x["id"]: x for x in json.load(open(base / judge_name, encoding="utf-8"))["details"]}
n = c = w = l = 0
for gf in sorted((base / "generated").glob("*.sql")):
    qid = gf.stem
    cands = candidates(gf.read_text(encoding="utf-8"))
    cv = cross[qid]
    pick = (cv["claude_agrees_with"] - 1) if cv["claude_agrees_with"] is not None else (judge[qid]["judged"] - 1)
    pick = min(pick, len(cands) - 1)
    gdf, _ = execute_sql_with_timeout((base / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip(), creds)
    gdf = gdf if gdf is not None else pd.DataFrame()
    def m(i):
        df, _ = execute_sql_with_timeout(cands[i], creds)
        return bool(compare_results(df if df is not None else pd.DataFrame(), gdf)[0])
    pm, c1m = m(pick), m(0)
    n += 1; c += pm
    if pm and not c1m: w += 1
    if c1m and not pm: l += 1
print(f"stack (cross -> {judge_name}): {c}/{n} = {100*c/n:.2f}%  (wins {w}, losses {l} vs candidate 1)")
