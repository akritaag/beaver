# myagent — Codex on BEAVER `dw`: results

Evaluation of the Codex-backed agent (`agent.py`) on the BEAVER `dw` benchmark.
All numbers are **execution accuracy** (generated SQL's result set equals gold's)
on the standard 100-question `dev_sampled` set, scored against the live MySQL
`dw` database. Generation uses the local Codex CLI (model `gpt-5.5` via ChatGPT
login). Every enhancement below is **gold-blind** — the agent never sees the
expected/correct answer.

> See `../claudeagent/RESULTS.md` for the Claude (`claude -p`) vs Codex
> head-to-head and the cross-model ensembling finding.

## Scoreboard (`dw`, 100 questions, `high` reasoning effort)

| Config | exec acc | correct | SQL errors | empty | mismatch |
|--------|:--------:|:-------:|:----------:|:-----:|:--------:|
| setting 1, one-shot (baseline) | 23% | 23 | 11 | 2 | 64 |
| setting 1 + self-fix | 23% | 23 | 0 | 5 | 72 |
| setting 1 + explore/verify | 26% | 26 | 5 | 3 | 66 |
| setting 2 + self-fix | 30% | 30 | 1 | 4 | 65 |
| **setting 2 + explore/verify + fix** | **34%** | **34** | **0** | 2 | 64 |

5-question sanity samples (setting 1): low 20%, high 40%, xhigh 40%.
Full-100, setting 1: high 23%, xhigh 25% (within run-to-run noise).

## Settings
- **setting 1** — hints: gold tables, column mapping, join keys.
- **setting 2** — setting 1 + domain knowledge + query decomposition.
- (setting 0 = retrieved tables only; not run here — needs `retrieve/`.)

## Enhancements (all in `agent.py`, all gold-blind)
- **self-fix** (`CODEX_SQL_FIX=1`) — runs its own SQL read-only; on an execution
  error, feeds back *only the DB error* and asks Codex to fix, looping up to
  `CODEX_FIX_ATTEMPTS`. Stops once the query executes. → guarantees executable
  output (SQL errors → 0); does not change correctness on its own.
- **explore/verify** (`CODEX_SQL_EXPLORE=1`) — the agent runs read-only queries
  against the real tables (sample rows, counts, its candidate query) and inspects
  the rows *its own* queries return, then self-checks and revises before
  finalizing. Never shown gold rows. → +3–4 pts.

## Findings
- **Lever ranking:** richer hints (+7) > explore/verify (+3–4) > self-fix
  (robustness, ~0 acc) ≈ reasoning effort (within noise, high↔xhigh).
- Levers **stack independently**: setting 2 (30%) + explore/verify + fix → **34%**.
- **explore and fix are complementary**: explore improves correctness; the final
  fix pass drives SQL errors to 0 (explore alone left 5).
- The dominant failure mode is **semantic** (~64 "values mismatch") at every
  config — queries execute but return the wrong rows. Even with all 5 oracle
  hints (setting 2), ~66% still miss, i.e. SQL *construction* on these enterprise
  schemas is hard even when schema-linking is handed over.
- **Headroom:** the union of correct sets across runs (~39) exceeds any single
  run, indicating self-consistency / majority-vote ensembling is the next lever.

## Hint ablation: is the decomposition hint worth it?
Controlled ablation (Codex, high + explore/verify + fix), setting 2 with vs
without `--decomp` (all else identical: gold tables + mapping + join keys +
domain knowledge):

| Config | exec acc | correct |
|--------|:--------:|:-------:|
| setting 2 (with decomp) | **34%** | 34 |
| setting 2 − decomp | 28% | 28 |

Per-question, decomposition **helps 13, hurts 7 → net +6**. It is net-**positive**
despite occasionally backfiring. Don't drop it.

## Failure analysis (setting 2 "values mismatch")
Two dissected mismatches — both cases where the *decomposition* hint embedded
gold-query scaffolding that contradicted the final ask, and the model baked it
into the answer:
- **dw_2933** — decomposition said "top 10 organizations"; the question asks "for
  each organization". Agent added `LIMIT 10` → 10 rows vs gold's 154.
- **dw_104** — decomposition mentioned "a window of 2 preceding and 1 following";
  the question wants the *overall* average. Claude used a rolling
  `AVG() OVER (... ROWS BETWEEN 2 PRECEDING AND 1 FOLLOWING)` (wrong per-row
  deviations); Codex used `AVG() OVER ()` (overall) and got it right — a clean
  backend-divergence case.

Caveat (see ablation above): these are a real failure *mode* but a *minority* —
the ablation shows decomposition is net-positive overall. Hand-picked errors
identify modes; only the controlled run gives net impact.

## Reproduce
```bash
# best config
cd eval/myagent
CODEX_REASONING_EFFORT=high CODEX_SQL_EXPLORE=1 CODEX_SQL_FIX=1 \
  ./run.sh --dataset dw --setting 2

# score (from eval/)
cd .. && uv run python evaluate_ex_acc.py --dataset dw \
  --input_dir unified-output/myagent/<run_name>
```
Requires the `dw` MySQL DB loaded + `MYSQL_*` creds in `.env`. Per-run SQL outputs
and `summary_ex_acc.json` are written under `eval/unified-output/myagent/`
(gitignored — contains gold SQL from the gated dataset).
