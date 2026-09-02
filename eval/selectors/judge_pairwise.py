"""Judge v3: pairwise, both-orders, probe-informed candidate selection.

Improvements over v1/v2 (motivated by the literature scan):
- PAIRWISE comparisons in BOTH orders (CHASE-SQL style) instead of pick-from-a-
  numbered-list, to neutralize position bias.
- Result-set DIFF shown to the judge: sample rows present in one candidate's
  result but not the other's, so the judge sees exactly where they disagree.
- Distinctness-axis probe: if two candidates' SQL becomes identical after
  stripping DISTINCT, the judge is told "these differ only on dedup; their
  differing results prove duplicates exist along the counted path; plain COUNT
  counts rows, DISTINCT counts entities."
- Runs ONLY on the cascade's tier B (questions where cross-model confirmation
  did not fire), per the cascade design. Tier A keeps the cross-confirmed pick.
- Pick-only: the judge never writes SQL (Ankit's negative-result design law).

Tournament: winner(c1 vs c2) vs c3, each pairing judged in both orders; a split
verdict keeps the lower-index candidate (c1-priority conservatism).

Usage (from eval/): python select_judge_v3.py <run_dir>
"""
import sys
import json
import re
import subprocess
from pathlib import Path
from _common import run_dir, candidates, dev_questions, CANDIDATE_SEP
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results

import os
import shutil

def _claude_cmd():
    """Portable claude invocation; CLAUDE_MODEL selects the judge model
    (e.g. claude-sonnet-4-5), default is the CLI's default (Opus 5)."""
    cmd = [os.getenv("CLAUDE_BIN") or shutil.which("claude") or "claude", "-p", "--output-format", "text"]
    if os.getenv("CLAUDE_MODEL"):
        cmd += ["--model", os.getenv("CLAUDE_MODEL")]
    return cmd

SEP = CANDIDATE_SEP

def norm_sql(s):
    s = re.sub(r"\s+", " ", s.upper())
    s = re.sub(r"COUNT\s*\(\s*DISTINCT\s+", "COUNT(", s)
    return s.strip()

def preview(df, err, n=6):
    if err is not None:
        return f"EXECUTION ERROR: {err[:250]}"
    if df is None or df.empty:
        return "EMPTY RESULT (0 rows)"
    s = df.head(n).to_string(max_cols=10)
    return f"{df.shape[0]} rows x {df.shape[1]} cols\n{s[:900]}"

def row_diff(dfa, dfb, n=4):
    try:
        sa = set(map(tuple, dfa.astype(str).itertuples(index=False)))
        sb = set(map(tuple, dfb.astype(str).itertuples(index=False)))
        only_a, only_b = list(sa - sb)[:n], list(sb - sa)[:n]
        return (f"rows only in FIRST ({len(sa-sb)} total): {only_a}\n"
                f"rows only in SECOND ({len(sb-sa)} total): {only_b}")[:700]
    except Exception:
        return "diff unavailable"

def ask(question, sql_a, prev_a, sql_b, prev_b, diff, axis_note):
    prompt = (
        "Two SQL candidates answer the same data-warehouse question. Do not use "
        "any tools; judge only from the information given.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"CANDIDATE FIRST SQL:\n{sql_a[:2200]}\nFIRST EXECUTED RESULT:\n{prev_a}\n\n"
        f"CANDIDATE SECOND SQL:\n{sql_b[:2200]}\nSECOND EXECUTED RESULT:\n{prev_b}\n\n"
        f"RESULT DIFF:\n{diff}\n\n"
        + (f"PROBE NOTE: {axis_note}\n\n" if axis_note else "")
        + "Which candidate's executed result correctly answers the question? "
        "A user asking to count named entities (courses, rooms, people) almost "
        "always means distinct entities when duplicates exist along the join "
        "path. An empty or error result is rarely right if the other returned "
        "sensible rows. Reply EXACTLY one word: FIRST or SECOND."
    )
    try:
        proc = subprocess.run(
            _claude_cmd(),
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=180,
        )
        out = (proc.stdout or "").strip().upper()
        if "FIRST" in out[:30] and "SECOND" not in out[:30]:
            return 0
        if "SECOND" in out[:30]:
            return 1
    except Exception:
        pass
    return None  # unparseable -> treated as split

def pair_winner(question, idx_a, idx_b, cands, execs):
    (dfa, ea), (dfb, eb) = execs[idx_a], execs[idx_b]
    axis = None
    if norm_sql(cands[idx_a]) == norm_sql(cands[idx_b]):
        axis = ("These two differ ONLY in COUNT vs COUNT(DISTINCT). Their results "
                "differ, which proves duplicate keys exist along the counted join "
                "path: plain COUNT counts rows, DISTINCT counts unique entities.")
    d = row_diff(dfa, dfb) if (dfa is not None and dfb is not None and ea is None and eb is None) else "n/a (an arm errored/empty)"
    v1 = ask(question, cands[idx_a], preview(dfa, ea), cands[idx_b], preview(dfb, eb), d, axis)
    v2 = ask(question, cands[idx_b], preview(dfb, eb), cands[idx_a], preview(dfa, ea), d, axis)
    # map order-2 verdict back: FIRST in order2 = idx_b
    picks = []
    if v1 is not None:
        picks.append(idx_a if v1 == 0 else idx_b)
    if v2 is not None:
        picks.append(idx_b if v2 == 0 else idx_a)
    if len(picks) == 2 and picks[0] == picks[1]:
        return picks[0]
    return min(idx_a, idx_b)  # split/unparseable -> lower index

def main():
    run = run_dir(sys.argv[1])
    creds = get_mysql_credentials("dw_real")
    import pandas as pd
    dev = {q: r["question"] for q, r in dev_questions().items()}
    cross = {x["id"]: x for x in json.load(open(run / "summary_crossvote.json", encoding="utf-8"))["details"]}

    n = correct = 0
    details = []
    for gf in sorted((run / "generated").glob("*.sql")):
        qid = gf.stem
        cv = cross[qid]
        cands = [c.strip() for c in gf.read_text(encoding="utf-8").split(SEP) if c.strip()] or [""]
        execs = [execute_sql_with_timeout(c, creds) for c in cands]

        if cv["claude_agrees_with"] is not None:
            pick = cv["claude_agrees_with"] - 1   # tier A: keep cross-confirmed pick
            tier = "A"
        else:
            tier = "B"
            pick = 0
            if len(cands) >= 2:
                pick = pair_winner(dev[qid], 0, 1, cands, execs)
            if len(cands) >= 3:
                pick = pair_winner(dev[qid], pick, 2, cands, execs)

        gold_df, _ = execute_sql_with_timeout((run / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip(), creds)
        if gold_df is None:
            gold_df = pd.DataFrame()
        df = execs[pick][0]
        if df is None:
            df = pd.DataFrame()
        m = bool(compare_results(df, gold_df)[0])
        n += 1; correct += m
        details.append({"id": qid, "tier": tier, "picked": pick + 1, "picked_match": m,
                        "candidate1_match": bool(compare_results(execs[0][0] if execs[0][0] is not None else pd.DataFrame(), gold_df)[0])})
        print(f"{qid}: tier{tier} pick c{pick+1} match={m}", flush=True)

    summary = {"total": n, "cascade_v3_accuracy": round(100 * correct / n, 2),
               "correct": correct}
    (run / "summary_judge_v3.json").write_text(
        json.dumps({"metrics": summary, "details": details}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
