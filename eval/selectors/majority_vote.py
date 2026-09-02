"""Gold-blind majority-vote selector over executed candidate result sets.

For each question: execute all 3 candidates, compare their result sets to EACH
OTHER (never to gold), and select the candidate belonging to the largest group
of agreeing results (ties -> lowest candidate index; no agreement -> candidate 1;
candidate 1 errored -> first non-erroring candidate). Only after selection is the
chosen candidate scored against gold. This is ReFoRCE-style majority voting,
kept strictly gold-blind: agreement computation never touches gold SQL/rows.

Run from eval/ with PYTHONPATH pointing at the venv site-packages.
Usage: python select_candidate.py <run_dir>   (run_dir has generated/ and gold/)
"""
import sys
import json
from pathlib import Path
from _common import run_dir, candidates, dev_questions, CANDIDATE_SEP
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results


def main():
    run_dir = run_dir(sys.argv[1])
    creds = get_mysql_credentials("dw_real")
    assert creds, "no MySQL creds"

    import pandas as pd
    n_total = n_sel_match = n_c1_match = n_any_match = 0
    details = []
    gen_files = sorted((run_dir / "generated").glob("*.sql"))
    for gf in gen_files:
        qid = gf.stem
        pred = gf.read_text(encoding="utf-8").strip()
        cands = [c.strip() for c in pred.split(CANDIDATE_SEP) if c.strip()] or [""]

        # execute every candidate (gold-blind)
        results = []  # (idx, df or None, err)
        for i, c in enumerate(cands):
            df, err = execute_sql_with_timeout(c, creds)
            results.append((i, df, err))

        ok = [(i, df) for i, df, err in results if df is not None and err is None]
        # agreement groups among executed candidates
        agree_count = {i: 0 for i, _ in ok}
        for a in range(len(ok)):
            for b in range(a + 1, len(ok)):
                ia, dfa = ok[a]
                ib, dfb = ok[b]
                m, _ = compare_results(dfa, dfb)
                if m:
                    agree_count[ia] += 1
                    agree_count[ib] += 1
        if ok:
            best = max(agree_count.values())
            if best > 0:
                sel_idx = min(i for i, v in agree_count.items() if v == best)
            else:
                sel_idx = 0 if any(i == 0 for i, _ in ok) else ok[0][0]
        else:
            sel_idx = 0  # everything errored; candidate 1 by convention

        # ---- gold contact only below this line ----
        gold_sql = (run_dir / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip()
        gold_df, gold_err = execute_sql_with_timeout(gold_sql, creds)
        if gold_df is None:
            gold_df = pd.DataFrame()

        def matches(idx):
            df = next((d for i, d, e in results if i == idx), None)
            if df is None:
                df = pd.DataFrame()
            m, _ = compare_results(df, gold_df)
            return bool(m)

        sel_m = matches(sel_idx)
        c1_m = matches(0)
        any_m = any(matches(i) for i, _, _ in results)

        n_total += 1
        n_sel_match += sel_m
        n_c1_match += c1_m
        n_any_match += any_m
        details.append({"id": qid, "n_candidates": len(cands),
                        "selected": sel_idx + 1, "selected_match": sel_m,
                        "candidate1_match": c1_m, "any_match": any_m,
                        "agreement": {str(k + 1): v for k, v in agree_count.items()}})

    summary = {
        "total": n_total,
        "candidate1_matches": n_c1_match,
        "candidate1_accuracy": round(100 * n_c1_match / n_total, 2),
        "selected_matches": n_sel_match,
        "selected_accuracy": round(100 * n_sel_match / n_total, 2),
        "pass_at_3_matches": n_any_match,
        "pass_at_3_accuracy": round(100 * n_any_match / n_total, 2),
    }
    out = run_dir / "summary_selector.json"
    out.write_text(json.dumps({"metrics": summary, "details": details}, indent=2),
                   encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"saved: {out}")

if __name__ == "__main__":
    main()
