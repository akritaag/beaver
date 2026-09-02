"""Compare an experimental arm (scored with evaluate_ex_acc --multi) against the
three full-stack seeds on the same question subset.

Usage (from eval/): python compare_subset.py <arm_run_dir> <label>
Prints c1 / pass@3 for the arm and for each seed restricted to the arm's
questions, plus the per-question ledger of arm c1 wins the seeds never had.
"""
import sys
import json
import glob
from pathlib import Path
from _common import run_dir, EVAL

arm_dir, label = run_dir(sys.argv[1]), sys.argv[2]
seeds = {
    "seed1": str(EVAL / "unified-output/myagent/codex-beaver-dw_real-setting2-log-20260827-233051"),
    "seed2": glob.glob(str(EVAL / "unified-output/myagent/codex-beaver-dw_real-setting2-var1-log-*"))[0],
    "seed3": glob.glob(str(EVAL / "unified-output/myagent/codex-beaver-dw_real-setting2-var2-log-*"))[0],
}

def load(d):
    det = json.load(open(Path(d) / "summary_ex_acc.json", encoding="utf-8"))["details"]
    return {x["file"].replace(".sql", ""): x for x in det}

arm = load(arm_dir)
qs = sorted(arm)
n = len(qs)
def acc(det, key):
    return sum(1 for q in qs if det.get(q, {}).get(key))

print(f"=== {label}: {n} questions ===")
print(f"{label:8} c1 {acc(arm,'candidate1_match')}/{n}   pass@3 {acc(arm,'match')}/{n}")
sd = {k: load(v) for k, v in seeds.items()}
for k, det in sd.items():
    print(f"{k:8} c1 {acc(det,'candidate1_match')}/{n}   pass@3 {acc(det,'match')}/{n}")

never_c1 = [q for q in qs if not any(sd[k][q].get("candidate1_match") for k in sd)]
arm_c1_wins = [q for q in qs if arm[q].get("candidate1_match")]
new_wins = [q for q in arm_c1_wins if q in never_c1]
print(f"\narm c1 hits: {len(arm_c1_wins)}; of which never c1-hit by any seed: {len(new_wins)} -> {new_wins}")
lost_p3 = [q for q in qs if not arm[q]["match"] and all(sd[k][q]["match"] for k in sd)]
print(f"pass@3 regressions (all 3 seeds hit, arm missed): {len(lost_p3)} -> {lost_p3}")
