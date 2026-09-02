#!/bin/bash
# Full re-score against the rebuilt (case-insensitive) database.
set -e
# run with the project venv active (or PY set to its python)
PY="${PY:-python}"
cd "$(dirname "$0")/../.."
SC="selectors"

S2="unified-output/myagent/codex-beaver-dw_real-setting2-log-20260827-233051"
S1="unified-output/myagent/codex-beaver-dw_real-setting1-log-20260831-224436"
CT="unified-output/myagent/codex-beaver-dw_real-setting2-fewshot-control-log-20260901-000305"
CL="unified-output/claudeagent/claude-beaver-dw_real-setting2-log-20260831-224441"
V1=$(ls -d unified-output/myagent/codex-beaver-dw_real-setting2-var1-log-* | head -1)

echo "=== gold audit on rebuilt DB ==="
"$PY" - <<'PYEOF'
import json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('../.env')
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout
creds = get_mysql_credentials("dw_real")
run = Path("unified-output/myagent/codex-beaver-dw_real-setting2-log-20260827-233051")
errs = []
empty = ok = 0
for gf in sorted((run / "gold").glob("*.sql")):
    df, err = execute_sql_with_timeout(gf.read_text(encoding="utf-8").strip(), creds)
    if err is not None: errs.append(gf.stem)
    elif df is None or df.empty: empty += 1
    else: ok += 1
print(f"gold ok+nonempty={ok} empty={empty} errored={len(errs)} -> {errs}")
PYEOF

for pair in "$S2:--multi" "$S1:--multi" "$V1:--multi" "$CT:" "$CL:"; do
  dir="${pair%%:*}"; flag="${pair##*:}"
  echo "=== scoring $dir $flag ==="
  "$PY" evaluate_ex_acc.py --dataset dw_real $flag --input_dir "$dir" 2>&1 | tail -12
done

echo "=== re-running cross-backend vote ==="
"$PY" "$SC/cross_backend_vote.py" "$S2" "$CL" 2>&1 | tail -8

echo "=== frozen stack (fresh cross pick -> stored v1 judge pick) vs new gold ==="
"$PY" - <<'PYEOF'
import json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('../.env')
from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results
import pandas as pd
base = Path("unified-output/myagent/codex-beaver-dw_real-setting2-log-20260827-233051")
creds = get_mysql_credentials("dw_real")
SEP = "-- ===CANDIDATE=== --"
cross = {x["id"]: x for x in json.load(open(base/"summary_crossvote.json", encoding="utf-8"))["details"]}
judge = {x["id"]: x for x in json.load(open(base/"summary_judge.json", encoding="utf-8"))["details"]}
n = c = 0
for gf in sorted((base/"generated").glob("*.sql")):
    qid = gf.stem
    cands = [x.strip() for x in gf.read_text(encoding="utf-8").split(SEP) if x.strip()] or [""]
    cv = cross[qid]
    pick = (cv["claude_agrees_with"] - 1) if cv["claude_agrees_with"] is not None else (judge[qid]["judged"] - 1)
    if pick >= len(cands): pick = 0
    df, err = execute_sql_with_timeout(cands[pick], creds)
    if df is None: df = pd.DataFrame()
    gdf, _ = execute_sql_with_timeout((base/"gold"/f"{qid}.sql").read_text(encoding="utf-8").strip(), creds)
    if gdf is None: gdf = pd.DataFrame()
    n += 1; c += bool(compare_results(df, gdf)[0])
print(f"STACK on rebuilt DB: {c}/{n} = {100*c/n:.2f}%")
PYEOF
echo "SWEEP_DONE"
