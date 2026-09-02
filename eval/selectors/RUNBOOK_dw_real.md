# Runbook: every number in `myagent/RESULTS_dw_real.md`, and the command behind it

All generation commands run from `eval/myagent` (or `eval/claudeagent`). All
scoring and selector commands run from `eval/`. The MySQL `dw` database must
be case-insensitive (`ops/rebuild_db.sh`); see the integrity notes in the
results file. Run directories land under `eval/unified-output/<agent>/<run>`.

## Generation

| Row in RESULTS | Command (from eval/myagent unless noted) |
|---|---|
| full stack, setting 2 (each seed is one run of this) | `CODEX_SQL_FIX=1 CODEX_SQL_EXPLORE=1 CODEX_STYLE_GUIDE=1 CODEX_SCHEMA_SKILL=1 CODEX_N_CANDIDATES=3 CODEX_REASONING_EFFORT=high ./run.sh --dataset dw_real --setting 2 --q_fn dev` |
| full stack, setting 1 | same flags, `--setting 1` |
| control (no techniques, one shot) | `CODEX_REASONING_EFFORT=high ./run.sh --dataset dw_real --setting 2 --q_fn dev` |
| claudeagent, explore+fix | from eval/claudeagent: `CLAUDE_SQL_FIX=1 CLAUDE_SQL_EXPLORE=1 CLAUDE_EFFORT=high ./run.sh --dataset dw_real --setting 2 --q_fn dev` |
| grain arm A (54 targets) | full-stack flags + `CODEX_GRAIN=1`, `--q_fn dev_grain --num_workers 2`; build `data/dw_real/dev_grain.json` by filtering `dev.json` to `myagent/grain_targets_dw_real.txt`; profile facts first with `python grain_profile.py --q_fn dev` |
| minus-style-guide ablation | full-stack flags without `CODEX_STYLE_GUIDE`, `--q_fn dev_grain` |
| grain rule, full 121 (= arm A + arm C) | arm A on `dev_grain` (54) and the same flags on `dev_grain_rest` (the other 67, `data/dw_real/dev_grain_rest.json` = complement of the target list); then `python selectors/merge_runs.py unified-output/myagent/<grain-full> <arm A dir> <arm C dir>` and score the merged dir with `--multi`; `selectors/concur.py` on it for the selector row |
| bracketing only (bare model, 3 candidates, no techniques) | `CODEX_N_CANDIDATES=3 CODEX_REASONING_EFFORT=high ./run.sh --dataset dw_real --setting 2 --q_fn dev`; then `selectors/concur.py` on it with the claudeagent run |

Notes: `run.sh` uses `uv run python`; if that is blocked, call `execute.py` and
`unify.py` directly with the same arguments (`--gold_tables --mapping
--join_keys --knowledge --decomp` is what `--setting 2` expands to). Four
workers can exhaust commit memory on a 32 GB machine when explore queries
return large results; two is safe. A question whose explore query hangs the
process can be generated alone with `CODEX_SQL_EXPLORE`/`CODEX_SQL_FIX` unset
and the run resumed (`--resume <output dir>`).

## Scoring

| Number | Command (from eval/) |
|---|---|
| cand-1 and pass@3 for any multi-candidate run | `uv run python evaluate_ex_acc.py --dataset dw_real --multi --input_dir unified-output/myagent/<run>` |
| control, claudeagent (single answer) | same without `--multi` |
| gold execution audit (15 fail on strict case) | `python selectors/gold_audit.py unified-output/myagent/<run>` |
| re-score everything after a DB rebuild | `bash selectors/ops/rescore_all.sh` (edit the run names at the top) |

`--multi` is mandatory for `CODEX_N_CANDIDATES>1` runs; without it the three
candidates are executed as one string.

## Concur end to end (the method as one command)

Stage 1 is two generation runs: the full-stack Codex run (3 candidates per
question) and the claudeagent run (1 answer per question), commands above.
Stage 2 is one script that combines them into a single-answer run directory:

    python selectors/concur.py unified-output/myagent/<codex run> unified-output/claudeagent/<claude run>
    python evaluate_ex_acc.py --dataset dw_real --input_dir unified-output/concur/<out>

It selects by cross-model concurrence where Claude's result matches a Codex
candidate, and otherwise by the judge (reusing `summary_judge.json` if
`judge.py` was already run, else calling `claude -p`). Scored with the plain
scorer, no `--multi`, it reproduces the 38.0 in the results table
(`concur_selection.json` records which rule chose each answer).

## Selectors (the individual policies, all on the frozen setting-2 seed-1 generations)

| Row in selector table | Command (from eval/) | Output file in run dir |
|---|---|---|
| majority vote over own candidates | `python selectors/majority_vote.py <run>` | `summary_selector.json` |
| LLM judge, eager | `python selectors/judge.py <run>` | `summary_judge.json` |
| LLM judge, conservative | `python selectors/judge.py <run> conservative` | `summary_judge_conservative.json` |
| cross-model confirmation | `python selectors/cross_backend_vote.py <codex run> <claudeagent run>` | `summary_crossvote.json` |
| stack (cross -> judge), plus tiers, modes, pass@2, clarification simulation | `python selectors/cascade_tiers.py <run>` | prints |
| stack re-scored against fresh gold (post-rebuild) | `python selectors/stack_score.py <run>` | prints |
| pairwise both-orders probe judge (v3) | `python selectors/judge_pairwise.py <run>` | `summary_judge_v3.json` |
| verbalized self-confidence baseline (tiers, risk-coverage, AUROC) | `python selectors/self_confidence.py <run> [--candidate 1]` | `summary_self_confidence.json` |

The judges call `claude -p` (Claude subscription); `cross_backend_vote.py`
and `majority_vote.py` only execute SQL. Judge picks are gold-blind, so
they remain valid after a database rebuild; only the scoring is repeated.

## Failure analysis

| Artifact | Command |
|---|---|
| per-question dossiers + INDEX | `python selectors/make_dossiers.py <run>` (writes `<run>/dossiers/`) |
| TAXONOMY.md | categorized by reading the dossiers (analysis agents, batch of ~18 each); not a script |
| grain target list | rows in TAXONOMY with the count-distinct/fan-out flag = Y; kept in `myagent/grain_targets_dw_real.txt` |
| arm vs three seeds on a subset | `python selectors/compare_subset.py <arm run> <label>` |

Dossiers, taxonomy, and run outputs contain gold SQL from the gated dataset
and stay gitignored.

## Number-to-source map (RESULTS_dw_real.md scoreboard, case-insensitive DB)

- control 35.5: control run, scored without `--multi`
- setting 1: 26.4 / 43.8: setting-1 run, `--multi`
- setting 1 control 29.8: `CODEX_REASONING_EFFORT=high ./run.sh --dataset dw_real --setting 1 --q_fn dev`, no `--multi`
- setting 1 claudeagent 29.8: claudeagent flags with `--setting 1`
- setting 1 Concur 29.8: `concur.py` on the setting-1 full-stack run + the setting-1 claudeagent run
- setting 2 seeds 29.8/28.1/29.8 and 47.9/46.3/47.9: three full-stack runs, `--multi`
- claudeagent 30.6: claudeagent run, no `--multi`
- bare model + 3 candidates 35.5 / 41.3: bracketing-only run (see Generation), `--multi`; Concur on it 33.9: `concur.py` on that run + the setting-2 claudeagent run
- cross-model selection 33.9: `cross_backend_vote.py` on seed 1 + claudeagent run
- stack 38.0: `concur.py` on seed 1 + the claudeagent run, scored with the plain scorer (equivalently `stack_score.py` on seed 1)
- tiers 57% / 17%, unanimity 77% on 18%, clarification +22 (29.8 to 47.9): `cascade_tiers.py` on seed 1 after `refresh_matches.py` (all flags re-evaluated on the rebuilt DB)
- taxonomy counts 40/26/20/2/1 and the 61% grain figure: `dossiers/TAXONOMY.md`
