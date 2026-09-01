# myagent + claudeagent on BEAVER `dw_real`: results

Evaluation on the **real-query split** (`dw_real`, 121 questions from actual MIT
warehouse query logs — the seed corpus the synthetic sets were expanded from).
Scored with `evaluate_ex_acc.py --dataset dw_real` against the live MySQL `dw`
database. Backends: Codex CLI (`gpt-5.6-sol`, ChatGPT login) and Claude Code CLI
(`claude-opus-5`). All techniques gold-blind; setting/hint definitions as in
`RESULTS.md`.

> **Scoring environment matters (see "Benchmark integrity"):** 12/121 gold
> queries reference lowercase table names and only execute where MySQL is
> case-insensitive (macOS default; Linux requires
> `--lower-case-table-names=1` at initialization). All numbers below are from
> a case-insensitive database, matching macOS scoring. Under a strict-case
> Linux default, every number drops 2–4 points from gold-side execution
> failures alone.

## Scoreboard (`dw_real`, full 121-question `dev`, `high` effort)

| Config | cand-1 | pass@3 |
|--------|:------:|:------:|
| **few-shot control** (setting 2 hints, NO techniques, 1 answer) | **35.5%** | — |
| full stack, setting 1 (schema hints only) | 26.4% | 43.8% |
| full stack, setting 2 (all hints), seed 1 | 29.8% | 47.9% |
| full stack, setting 2, seed 2 | 28.1% | 46.3% |
| claudeagent, setting 2 (explore+fix only, 1 answer) | 30.6% | — |
| **frozen selector stack (cross-confirm → judge) on seed 1** | **38.0%** | — |

Full stack = `CODEX_SQL_FIX=1 CODEX_SQL_EXPLORE=1 CODEX_STYLE_GUIDE=1
CODEX_SCHEMA_SKILL=1 CODEX_N_CANDIDATES=3` (the `dw` best config from
`RESULTS.md`). **Run-to-run variance is small**: two independent seeds differ
by ~1.5 points on both cand-1 and pass@3, so deltas above ~3 points are
meaningful. Matched-candidate histogram (seed 1): candidate 2 recovers 2× more
than on `dw` — real questions are more ambiguous.

## Headline findings

**1. The dw-fit style guide transfers NEGATIVELY to real queries.** The
model-matched control (same model/effort/hints, zero techniques) beats the full
stack's candidate 1 by ~6 points (35.5 vs 29.8). Failure taxonomy over all 89
candidate-1 misses: **~61% involve COUNT vs COUNT(DISTINCT) / join-fan-out
grain** — `dw_real` gold dedups pervasively, and style-guide rule 3 ("no
DISTINCT unless the question says unique"), net-positive on `dw`, forces
candidate 1 wrong here. A convention prior fit on synthetic questions is
anti-correlated with real-query conventions. Planned: minus-style-guide
ablation; grain-aware disambiguation (inject profiled key-cardinality/fan-out
facts so the DISTINCT choice comes from data, not a static rule).

**2. The techniques' value routes through selection, not through candidate 1.**
The full pipeline (3 candidates + gold-blind selector stack) reaches **38.0%**,
beating the control's 35.5% — even though its raw candidate 1 loses to the
control. Ambiguity bracketing plus selection converts what convention priors
cannot.

**3. Selector results** (frozen setting-2 seed-1 generations, all gold-blind):

| Selector policy | accuracy | wins/losses vs always-c1 |
|---|:---:|:---:|
| always candidate 1 | 29.8% | — |
| mechanical majority vote over own 3 candidates | 29.8% | 0/0 |
| LLM judge (claude reads executed result previews), eager | +4 pts | 13/8 |
| cross-backend confirmation (Claude result matches a Codex candidate) | 33.9% | **5/0** |
| **cross-confirmation → eager-judge fallback** | **38.0%** | 12/1 |
| pairwise both-orders probe judge (v3) | below v1 | over-switches |

- Majority voting recovers **nothing**: within-model candidates are correlated
  voters (16/22 band questions have zero pairwise agreement; 6 agree on the
  wrong answer). Consistent with published correlated-judge results
  (`eval/READING_LIST.md`).
- Cross-model agreement is a zero-loss intervention (5 wins, 0 losses — held
  across both scoring regimes) and a strong confidence tier.
- Pick-only judging works where the `RESULTS.md` editing reviewer hurt; a
  fancier pairwise redesign underperformed the simple eager judge (positive
  probe signal, over-aggressive switching).

**4. Confidence tiers / deployment framing** (strict-case regime; to be
refreshed): within-model unanimity ≈ 73% accurate (18% of questions);
cross-confirmed ≈ 51% (42%); unconfirmed ≈ 16%. Candidate disagreement doubles
as a gold-blind ambiguity detector: allowing one clarifying question on flagged
cases is worth up to +18 points (the pass@3 band, converted through interaction
instead of guessing).

**5. Failure taxonomy** (89 c1-misses, per-question dossiers): underdetermined
40, gold-suspect 26, model-error 20, evaluator-artifact 2, **hint-backfire 1**
(vs 10 on `dw` — real questions' decomposition hints rarely contradict the
final question).

## Benchmark integrity findings (for upstream report)

1. **12 gold queries reference lowercase table names** (`dw.employee_directory`,
   `dw.academic_terms`, ...): scoring is platform-dependent unless the MySQL
   server is case-insensitive. Recommend documenting
   `--lower-case-table-names=1` in the benchmark setup instructions.
2. **3 gold queries have broken column refs** (dw_real_20/49/57) — fail on any
   platform.
3. ~11 further golds are semantically suspect per the taxonomy
   (fan-out-inflated aggregates; output columns contradicting the question
   text). Same class the paper's own error analysis documents.

## Portability

The fixes in this branch make both agents platform-neutral with no
configuration: prompts go over stdin with explicit UTF-8 (Windows'
command-line length cap and ANSI default encoding are both bypassed; no-ops
elsewhere), and the CLI binary is resolved via `shutil.which` (`codex.cmd` on
Windows, `codex` on macOS/Linux). `CODEX_BIN`/`CLAUDE_BIN` remain available
as explicit overrides.

## Reproduce

```bash
# full stack, real-query split
cd eval/myagent
CODEX_SQL_FIX=1 CODEX_SQL_EXPLORE=1 CODEX_STYLE_GUIDE=1 CODEX_SCHEMA_SKILL=1 \
CODEX_N_CANDIDATES=3 CODEX_REASONING_EFFORT=high \
  ./run.sh --dataset dw_real --setting 2 --q_fn dev

# score (from eval/) — DB must be case-insensitive (see integrity notes)
uv run python evaluate_ex_acc.py --dataset dw_real --multi \
  --input_dir unified-output/myagent/<run_name>
```

In flight at time of writing: third seed of the full stack; refreshed
confidence-tier numbers on the corrected scoring; grain-aware disambiguation
experiment. Selector scripts currently live outside the repo (session
scratchpad); to be added under `eval/selectors/` once stabilized.
