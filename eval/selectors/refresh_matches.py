"""Recompute the gold-match flags stored in the selector/judge summaries
against the CURRENT database, without any LLM calls. Picks (which candidate
each policy chose) are gold-blind and are kept; only *_match fields change.
Run after a database rebuild so every summary reflects the same scoring
regime. Usage: python refresh_matches.py <run_dir>
"""
import sys
import json
import pandas as pd
from _common import run_dir, candidates
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results

base = run_dir(sys.argv[1])
creds = get_mysql_credentials("dw_real")

# one DB pass: per question, which candidates match fresh gold
match = {}
for gf in sorted((base / "generated").glob("*.sql")):
    qid = gf.stem
    cands = candidates(gf.read_text(encoding="utf-8"))
    gdf, _ = execute_sql_with_timeout((base / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip(), creds)
    gdf = gdf if gdf is not None else pd.DataFrame()
    flags = []
    for c in cands:
        df, _ = execute_sql_with_timeout(c, creds)
        flags.append(bool(compare_results(df if df is not None else pd.DataFrame(), gdf)[0]))
    match[qid] = flags

def refresh(name, pick_field, match_field):
    p = base / name
    if not p.exists():
        return
    doc = json.load(open(p, encoding="utf-8"))
    changed = 0
    for x in doc["details"]:
        f = match[x["id"]]
        new = {
            "candidate1_match": f[0],
            "any_match": any(f),
            match_field: f[min(int(x[pick_field]) - 1, len(f) - 1)],
        }
        for k, v in new.items():
            if k in x and x[k] != v:
                changed += 1
            x[k] = v
    # recompute headline metrics where the file has them
    n = len(doc["details"])
    m = doc.get("metrics", {})
    if "candidate1_accuracy" in m:
        c1 = sum(x["candidate1_match"] for x in doc["details"])
        m["candidate1_matches"] = c1; m["candidate1_accuracy"] = round(100 * c1 / n, 2)
    for key, acc in (("selected_matches", "selected_accuracy"), ("judged_matches", "judged_accuracy")):
        if acc in m:
            s = sum(x[match_field] for x in doc["details"])
            m[key] = s; m[acc] = round(100 * s / n, 2)
    if "pass_at_3_accuracy" in m:
        a = sum(x["any_match"] for x in doc["details"])
        m["pass_at_3_matches"] = a; m["pass_at_3_accuracy"] = round(100 * a / n, 2)
    json.dump(doc, open(p, "w", encoding="utf-8"), indent=2)
    print(f"{name}: refreshed ({changed} flags changed)")

refresh("summary_selector.json", "selected", "selected_match")
refresh("summary_judge.json", "judged", "judged_match")
refresh("summary_judge_conservative.json", "judged", "judged_match")
refresh("summary_judge_v3.json", "picked", "picked_match")
