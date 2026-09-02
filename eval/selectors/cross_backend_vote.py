"""Cross-backend agreement selector: Codex's 3 candidates + Claude's answer.

Rule (gold-blind): execute all four. If Claude's result set matches some Codex
candidate k (lowest k on ties), select Codex candidate k — independent
cross-model confirmation. Otherwise keep Codex candidate 1. Gold is touched
only afterward, for scoring.

Usage: python cross_backend_vote.py <codex_run_dir> <claude_run_dir>
"""
import sys
import json
from pathlib import Path
from _common import run_dir, candidates, dev_questions, CANDIDATE_SEP
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results

SEP = CANDIDATE_SEP

def main():
    codex_dir, claude_dir = run_dir(sys.argv[1]), run_dir(sys.argv[2])
    creds = get_mysql_credentials("dw_real")
    import pandas as pd
    n = correct = wins = losses = confirmed = 0
    details = []
    for gf in sorted((codex_dir / "generated").glob("*.sql")):
        qid = gf.stem
        cands = [c.strip() for c in gf.read_text(encoding="utf-8").split(SEP) if c.strip()] or [""]
        cl_path = claude_dir / "generated" / f"{qid}.sql"
        cl_sql = cl_path.read_text(encoding="utf-8").strip() if cl_path.exists() else ""

        c_execs = [execute_sql_with_timeout(c, creds) for c in cands]
        cl_df, cl_err = execute_sql_with_timeout(cl_sql, creds) if cl_sql else (None, "missing")

        pick = 0
        agree_with = None
        if cl_df is not None and cl_err is None:
            for k, (df, err) in enumerate(c_execs):
                if df is not None and err is None and compare_results(df, cl_df)[0]:
                    pick, agree_with = k, k + 1
                    break

        gold_df, _ = execute_sql_with_timeout(
            (codex_dir / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip(), creds)
        if gold_df is None:
            gold_df = pd.DataFrame()

        def match(i):
            df = c_execs[i][0]
            if df is None:
                df = pd.DataFrame()
            return bool(compare_results(df, gold_df)[0])

        sel_m, c1_m = match(pick), match(0)
        n += 1; correct += sel_m
        confirmed += agree_with is not None
        if sel_m and not c1_m: wins += 1
        if c1_m and not sel_m: losses += 1
        details.append({"id": qid, "picked": pick + 1, "claude_agrees_with": agree_with,
                        "picked_match": sel_m, "candidate1_match": c1_m})

    summary = {"total": n, "accuracy": round(100 * correct / n, 2),
               "claude_confirmed_some_candidate": confirmed,
               "wins_vs_c1": wins, "losses_vs_c1": losses}
    (codex_dir / "summary_crossvote.json").write_text(
        json.dumps({"metrics": summary, "details": details}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
