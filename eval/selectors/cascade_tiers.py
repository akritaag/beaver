"""Confidence tiers, deployment modes, clarification simulation, pass@2, and
the offline selector combinations reported in RESULTS_dw_real.md.

Needs, in <run_dir>: summary_ex_acc.json (from evaluate_ex_acc --multi),
summary_selector.json (majority_vote.py), summary_crossvote.json
(cross_backend_vote.py), summary_judge.json (judge.py); optionally
summary_judge_conservative.json (judge.py <run> conservative).

Usage: python cascade_tiers.py <run_dir>
"""
import sys
import json
from _common import run_dir

base = run_dir(sys.argv[1])

def load(name):
    p = base / name
    if not p.exists():
        return None
    return {x.get("id") or x["file"].replace(".sql", ""): x
            for x in json.load(open(p, encoding="utf-8"))["details"]}

ex = load("summary_ex_acc.json")
sel = load("summary_selector.json")
cross = load("summary_crossvote.json")
judge = load("summary_judge.json")
jcons = load("summary_judge_conservative.json")
qs = sorted(ex)
n = len(qs)

print(f"=== {base.name}: {n} questions ===")

# pass@k and matched-candidate histogram
hist = {1: 0, 2: 0, 3: 0}
for q in qs:
    mc = ex[q].get("matched_candidate")
    if mc:
        hist[mc] = hist.get(mc, 0) + 1
c1 = hist[1]; p2 = hist[1] + hist[2]; p3 = sum(hist.values())
print(f"candidate-1 {c1}/{n} = {100*c1/n:.1f}%   pass@2 {p2}/{n} = {100*p2/n:.1f}%   pass@3 {p3}/{n} = {100*p3/n:.1f}%")
print(f"matched-candidate histogram c1/c2/c3: {hist[1]}/{hist[2]}/{hist[3]}")

if cross and judge:
    A = [q for q in qs if cross[q]["claude_agrees_with"] is not None]
    B = [q for q in qs if cross[q]["claude_agrees_with"] is None]
    accA = sum(cross[q]["picked_match"] for q in A)
    accB = sum(judge[q]["judged_match"] for q in B)
    c1B = sum(cross[q]["candidate1_match"] for q in B)
    print("\n=== cascade (cross-confirmation -> judge) ===")
    print(f"tier A cross-confirmed: {len(A)}q, acc {accA}/{len(A)} = {100*accA/max(1,len(A)):.1f}%")
    print(f"tier B unconfirmed:     {len(B)}q, c1 acc {c1B}/{len(B)} = {100*c1B/max(1,len(B)):.1f}%, judge acc {accB}/{len(B)} = {100*accB/max(1,len(B)):.1f}%")
    print(f"answer-all cascade:     {accA+accB}/{n} = {100*(accA+accB)/n:.1f}%")
    print(f"selective (tier A only): coverage {100*len(A)/n:.0f}%, precision {100*accA/max(1,len(A)):.1f}%")

    def combo(fallback_pick):
        c = w = l = 0
        for q in qs:
            m = cross[q]["picked_match"] if cross[q]["claude_agrees_with"] is not None else fallback_pick(q)
            c += m
            if m and not cross[q]["candidate1_match"]: w += 1
            if cross[q]["candidate1_match"] and not m: l += 1
        return c, w, l
    c, w, l = combo(lambda q: judge[q]["judged_match"])
    print(f"cross -> eager judge:        {c}/{n} = {100*c/n:.1f}%  (wins {w}, losses {l})")
    if jcons:
        c, w, l = combo(lambda q: jcons[q]["judged_match"])
        print(f"cross -> conservative judge: {c}/{n} = {100*c/n:.1f}%  (wins {w}, losses {l})")
        def inter(q):
            a, b = judge[q], jcons[q]
            if a["judged"] == b["judged"] and a["judged"] != 1:
                return a["judged_match"]
            return cross[q]["candidate1_match"]
        c, w, l = combo(inter)
        print(f"cross -> judge intersection: {c}/{n} = {100*c/n:.1f}%  (wins {w}, losses {l})")
        c = w = l = 0
        for q in qs:
            a, b = judge[q], jcons[q]
            m = a["judged_match"] if (a["judged"] == b["judged"] and a["judged"] != 1) else cross[q]["candidate1_match"]
            c += m
            if m and not cross[q]["candidate1_match"]: w += 1
            if cross[q]["candidate1_match"] and not m: l += 1
        print(f"judge intersection alone:    {c}/{n} = {100*c/n:.1f}%  (wins {w}, losses {l})")

if sel:
    print("\n=== within-model agreement as confidence; clarification simulation ===")
    flagged, unflagged = [], []
    for q in qs:
        a = sel[q]["agreement"]
        k = len(a)
        (unflagged if (k >= 2 and all(v == k - 1 for v in a.values())) else flagged).append(q)
    u_acc = sum(sel[q]["candidate1_match"] for q in unflagged)
    f_c1 = sum(sel[q]["candidate1_match"] for q in flagged)
    f_any = sum(sel[q]["any_match"] for q in flagged)
    print(f"unanimous (all candidates agree): {len(unflagged)}q ({100*len(unflagged)/n:.0f}%), c1 acc {u_acc}/{len(unflagged)} = {100*u_acc/max(1,len(unflagged)):.1f}%")
    print(f"flagged (candidates disagree):    {len(flagged)}q ({100*len(flagged)/n:.0f}%), c1 acc {f_c1}/{len(flagged)}, any-candidate {f_any}/{len(flagged)}")
    print(f"never ask: {u_acc+f_c1}/{n} = {100*(u_acc+f_c1)/n:.1f}%   ->  one clarification on flagged: {u_acc+f_any}/{n} = {100*(u_acc+f_any)/n:.1f}%  (+{f_any-f_c1})")
