"""Merge unified run directories with disjoint question sets into one, so the
standard scorer and concur.py can treat generations produced in batches under
one configuration as a single run. Refuses overlapping question ids.

Usage (from eval/): python merge_runs.py <out_dir> <run_dir> [<run_dir> ...]
"""
import sys
import shutil
from pathlib import Path
from _common import run_dir, EVAL

out = Path(sys.argv[1])
out = out if out.is_absolute() else EVAL / out
srcs = [run_dir(p) for p in sys.argv[2:]]
(out / "generated").mkdir(parents=True, exist_ok=True)
(out / "gold").mkdir(parents=True, exist_ok=True)
seen = {}
for s in srcs:
    for gf in sorted((s / "generated").glob("*.sql")):
        if gf.stem in seen:
            raise SystemExit(f"question {gf.stem} present in both {seen[gf.stem].name} and {s.name}")
        seen[gf.stem] = s
        shutil.copy(gf, out / "generated" / gf.name)
        shutil.copy(s / "gold" / gf.name, out / "gold" / gf.name)
print(f"merged {len(seen)} questions from {len(srcs)} runs -> {out}")
for s in srcs:
    print(f"  {s.name}: {sum(1 for v in seen.values() if v == s)}")
