# myagent + claudeagent on BEAVER `dw_real`: results

Evaluation on the **real-query split** (`dw_real`, 121 questions from actual MIT
warehouse query logs — the seed corpus the synthetic sets were expanded from).
Scored with `evaluate_ex_acc.py --dataset dw_real` against the live MySQL `dw`
database. Backends: Codex CLI (`gpt-5.6-sol`, ChatGPT login) and Claude Code CLI
(`claude-opus-5`). All techniques gold-blind; setting/hint definitions as in
`RESULTS.md`.

> **Provisional caveat (see "Benchmark integrity" below):** 15/121 gold queries
> do not execute on a Linux MySQL container — 12 due to lowercase table names
> (they resolve on macOS's case-insensitive MySQL, i.e. scoring is
> platform-dependent), 3 due to broken column refs. All numbers here were
> scored under the strict-case regime and will be re-scored after a
> case-insensitive DB rebuild (`--lower-case-table-names=1`).

## Scoreboard (`dw_real`, full 121-question `dev`, `high` effort)

| Config | cand-1 | pass@2 | pass@3 |
|--------|:------:|:------:|:------:|
| **few-shot control** (setting 2 hints, NO techniques, 1 answer) | **33.1%** | — | — |
| full stack, setting 1 (schema hints only) | 23.1% | — | 39.7% |
| full stack, setting 2 (all hints) | 26.4% | 41.3% | 44.6% |
| claudeagent, setting 2 (explore+fix only, 1 answer) | 29.8% | — | — |

Full stack = `CODEX_SQL_FIX=1 CODEX_SQL_EXPLORE=1 CODEX_STYLE_GUIDE=1
CODEX_SCHEMA_SKILL=1 CODEX_N_CANDIDATES=3` (the `dw` best config from
`RESULTS.md`). Matched-candidate histogram 32/18/4 — candidate 2 recovers 2×
more here than on `dw` (18 vs 9): real questions are more ambiguous.

## Headline negative result: the style guide transfers NEGATIVELY

The model-matched control (same model, same effort, same hints, zero
techniques) **beats** the full stack's candidate 1 by +6.6 points. Failure
taxonomy over all 89 candidate-1 misses (per-question dossiers, five parallel
analysis passes) explains it: **~61% of misses (54/89) involve COUNT vs
COUNT(DISTINCT) / join-fan-out grain** — `dw_real` gold dedups with DISTINCT
pervasively, and style-guide rule 3 ("no DISTINCT unless the question says
unique"), net-positive on `dw`, systematically forces candidate 1 wrong here.
A benchmark-convention prior fit on synthetic questions is anti-correlated
with real-query conventions. Planned: minus-style-guide ablation; grain-aware
disambiguation (inject profiled key-cardinality/fan-out facts so the DISTINCT
choice is made from data, not a static rule) targeting the 54 flagged questions.

Taxonomy totals (vs the `dw` taxonomy in `RESULTS.md`): underdetermined 40,
gold-suspect 26, model-error 20, evaluator-artifact 2, **hint-backfire 1**
(was 10 on `dw` — real questions' decomposition hints rarely contradict the
final question).

## Selector experiments (frozen setting-2 generations, all gold-blind)

| Selector policy | cand-1 acc | wins/losses vs always-c1 |
|---|:---:|:---:|
| always candidate 1 (baseline) | 26.4% | — |
| mechanical majority vote over own 3 candidates | 26.4% | 0/0 |
| LLM judge (claude reads executed result previews), eager | 30.6% | 13/8 |
| LLM judge, conservative prompt | 28.9% | 7/4 |
| cross-backend confirmation (Claude result matches a Codex candidate) | 30.6% | **5/0** |
| **cross-confirmation → eager-judge fallback (stack)** | **35.5%** | 12/1 |

- Majority voting recovers **nothing**: on the 22-question band (c1 wrong, some
  candidate right), 16 have zero pairwise agreement and 6 have agreement on the
  *wrong* answer — within-model candidates are correlated voters (consistent
  with published correlated-judge results; see `eval/READING_LIST.md`).
- Cross-model agreement is a zero-loss intervention signal: 9 pick-changes,
  5 wins, 0 losses. As a *confidence* tier: confirmed questions score 51.0%,
  unconfirmed 15.7% on c1 (3.2× ratio, fully gold-blind).
- The judge is pick-only, never edits — consistent with the `RESULTS.md`
  negative result on editing reviewers; picking works where editing hurt.

## Deployment framing (risk-coverage)

| Operating mode | coverage | accuracy |
|---|:---:|:---:|
| answer all, candidate 1 | 100% | 26.4% |
| answer all, cascade (confirm → judge) | 100% | 35.5% |
| selective: answer only cross-confirmed | 42% | 51.0% |
| within-model unanimity tier (all 3 candidates agree) | 18% | 72.7% |
| + one clarifying question when candidates disagree | 100% (asks 82%) | ≤44.6% |

Candidate disagreement doubles as a gold-blind ambiguity detector: allowing the
system one clarifying question on flagged cases is worth up to +18 points —
the pass@3 band converted through interaction instead of guessing.

## Benchmark integrity findings (for upstream report)

1. **12 gold queries reference lowercase table names** (`dw.employee_directory`,
   `dw.academic_terms`, ...): execute on macOS MySQL, error on Linux
   (`lower_case_table_names=0`). Scoring is platform-dependent.
2. **3 gold queries have broken column refs** (dw_real_20/49/57) — fail on any
   platform.
3. The "30 empty-gold questions" are therefore 15 true empties + 15 gold
   execution failures; empty-vs-empty matches on the latter are artifacts.
4. ~11 further golds are semantically suspect per the taxonomy
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
# full stack, real-query split (Windows: prepend CODEX_BIN=codex.cmd)
cd eval/myagent
CODEX_SQL_FIX=1 CODEX_SQL_EXPLORE=1 CODEX_STYLE_GUIDE=1 CODEX_SCHEMA_SKILL=1 \
CODEX_N_CANDIDATES=3 CODEX_REASONING_EFFORT=high \
  ./run.sh --dataset dw_real --setting 2 --q_fn dev

# score (from eval/)
uv run python evaluate_ex_acc.py --dataset dw_real --multi \
  --input_dir unified-output/myagent/<run_name>
```

In flight at time of writing: repeat-run variance (2 further seeds of the full
stack), pairwise probe-informed judge v3, case-insensitive DB rebuild + full
re-score, grain-aware disambiguation experiment. Selector scripts currently
live outside the repo (session scratchpad); to be added under `eval/selectors/`
after the re-score validates them.
