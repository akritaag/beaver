"""Verbalized self-confidence baseline for the confidence-tier claim.

For each question, ask the generator's own model (Codex CLI) to rate its
confidence 0-100 that its query answers the question, given the question, the
SQL, and the executed result preview. Never shown gold. Then report accuracy
in the top-confidence tier at the same coverage as the cross-model tier (42%),
a risk-coverage table, and AUROC of the score against correctness, so it can
be compared line by line with cross_backend_vote.py / cascade_tiers.py.

Usage (from eval/): python self_confidence.py <run_dir> [--candidate 1]
Writes <run_dir>/summary_self_confidence.json. Requires `codex` on PATH.
"""
import os
import re
import sys
import json
import shutil
import argparse
import subprocess
import pandas as pd
from _common import run_dir, candidates, dev_questions
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--candidate", type=int, default=1)
ap.add_argument("--coverage", type=float, default=0.42)
a = ap.parse_args()
base = run_dir(a.run)
creds = get_mysql_credentials("dw_real")
questions = dev_questions()
codex = os.getenv("CODEX_BIN") or shutil.which("codex") or "codex"


def preview(df, err, n=6):
    if err is not None:
        return f"EXECUTION ERROR: {err[:200]}"
    if df is None or df.empty:
        return "EMPTY RESULT (0 rows)"
    return f"{df.shape[0]} rows x {df.shape[1]} cols\n{df.head(n).to_string(max_cols=10)[:800]}"


def ask(question, sql, prev):
    prompt = (
        "You wrote the SQL below for the question below, and it was executed. "
        "Rate your confidence, from 0 to 100, that this query's result correctly "
        "answers the question. Consider ambiguity in the question, whether the "
        "result shape is plausible, and whether duplicates or missing rows are "
        "likely. Reply with ONLY an integer 0-100.\n\n"
        f"QUESTION:\n{question}\n\nSQL:\n{sql[:2500]}\n\nEXECUTED RESULT:\n{prev}\n"
    )
    import tempfile
    fd, last_msg = tempfile.mkstemp(prefix="conf_", suffix=".txt")
    os.close(fd)
    try:
        # -o writes only the model's final message, avoiding codex's stdout
        # header/footer (which includes a "tokens used" number).
        subprocess.run([codex, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-o", last_msg, "-"],
                       input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=180)
        text = open(last_msg, encoding="utf-8").read() if os.path.exists(last_msg) else ""
        m = re.findall(r"\b(\d{1,3})\b", text)
        v = int(m[0]) if m else None
        return v if v is not None and 0 <= v <= 100 else None
    except Exception:
        return None
    finally:
        try:
            os.remove(last_msg)
        except OSError:
            pass


rows = []
for gf in sorted((base / "generated").glob("*.sql")):
    qid = gf.stem
    cands = candidates(gf.read_text(encoding="utf-8"))
    sql = cands[min(a.candidate - 1, len(cands) - 1)]
    df, err = execute_sql_with_timeout(sql, creds)
    gdf, _ = execute_sql_with_timeout((base / "gold" / f"{qid}.sql").read_text(encoding="utf-8").strip(), creds)
    ok = bool(compare_results(df if df is not None else pd.DataFrame(), gdf if gdf is not None else pd.DataFrame())[0])
    conf = ask(questions[qid]["question"], sql, preview(df, err))
    rows.append({"id": qid, "confidence": conf, "correct": ok})
    print(f"{qid}: conf={conf} correct={ok}", flush=True)

scored = [r for r in rows if r["confidence"] is not None]
scored.sort(key=lambda r: -r["confidence"])
n = len(scored)
k = max(1, round(a.coverage * n))
top = scored[:k]
print(f"\nrated {n}/{len(rows)}; overall accuracy {sum(r['correct'] for r in scored)}/{n}")
print(f"top-confidence tier at {100*a.coverage:.0f}% coverage: {sum(r['correct'] for r in top)}/{k} = {100*sum(r['correct'] for r in top)/k:.1f}%")
print("risk-coverage:")
for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
    kk = max(1, round(frac * n)); t = scored[:kk]
    print(f"  coverage {100*frac:3.0f}%: precision {100*sum(r['correct'] for r in t)/kk:5.1f}%")
# AUROC by rank statistic
pos = [r["confidence"] for r in scored if r["correct"]]
neg = [r["confidence"] for r in scored if not r["correct"]]
if pos and neg:
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    print(f"AUROC {wins/(len(pos)*len(neg)):.3f}")
json.dump({"rows": rows}, open(base / "summary_self_confidence.json", "w", encoding="utf-8"), indent=2)
