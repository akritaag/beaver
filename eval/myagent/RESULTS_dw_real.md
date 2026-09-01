# myagent + claudeagent on BEAVER `dw_real`: results

Same protocol as `RESULTS.md`, on the real-query split: 121 questions from
actual MIT warehouse query logs (`dw_real/dev.json` — the seed corpus the
synthetic sets were expanded from). Codex CLI = `gpt-5.6-sol` via ChatGPT
login; Claude CLI = `claude-opus-5`. Everything gold-blind.

Scoring gotcha first, because it moves every number: 12/121 gold queries
reference lowercase table names (`dw.employee_directory`, ...). MySQL on macOS
is case-insensitive and runs them; a default Linux server errors them out, and
the scorer then treats gold as empty. Numbers below are from a rebuilt DB with
`--lower-case-table-names=1` (= macOS-comparable). On a strict-case server
everything drops 2–4 points from gold-side failures alone. Three further golds
(dw_real_20/49/57) have broken column refs and fail everywhere.

## Scoreboard (`dw_real`, full 121-question `dev`, high effort)

| Config | cand-1 | pass@3 |
|--------|:------:|:------:|
| setting 2, one-shot, no techniques (control) | 35.5% | — |
| full stack, setting 1 | 26.4% | 43.8% |
| full stack, setting 2 | 29.8% | 47.9% |
| full stack, setting 2, second seed | 28.1% | 46.3% |
| claudeagent, setting 2, explore+fix | 30.6% | — |
| cross-model selection (see below) | 33.9% | — |
| **cross-model → judge selector stack** | **38.0%** | — |

Full stack = the `dw` best config (fix + explore + style guide + schema skill +
3 candidates). Two independent seeds differ by ~1.5 pts on both metrics, so
run-to-run noise is small; deltas ≥3 pts are real. Candidate match histogram
36/18/4 — candidate 2 carries 2× the weight it did on `dw` (18 vs 9); real
questions are more ambiguous than the synthetic ones.

## The control beats the stack's candidate 1. The style guide is why.

35.5 vs 29.8: the no-technique control wins by ~6. Taxonomy of all 89
candidate-1 misses (per-question dossiers: question + hints + gold + all
candidates + executed rows): ~61% involve COUNT vs COUNT(DISTINCT) or
fan-out-inflated aggregates. `dw_real` gold dedups by default; style-guide
rule 3 says the opposite ("no DISTINCT unless the question says unique") and
was fit on `dw`, where it gained +3. Here it systematically points candidate 1
the wrong way. The house prior doesn't transfer from synthetic to real — it
anti-transfers.

But the pipeline still wins end-to-end: 3 candidates + a gold-blind selector →
38.0, above the control's 35.5. Candidate 1 loses; the bracket + selection
recovers more than rule 3 costs. The techniques' value routes through
selection, not through the first guess.

Taxonomy totals: underdetermined 40, gold-suspect 26 (12 = the lowercase golds,
3 = broken columns, ~11 semantic), model-error 20, evaluator-artifact 2,
hint-backfire 1. On `dw` hint-backfire was 10/65; on real questions the
decomposition hints almost never contradict the final question.

## Selectors (frozen setting-2 generations)

| Policy | acc | wins/losses vs c1 |
|--------|:---:|:---:|
| candidate 1 always | 29.8 | — |
| majority vote over own 3 candidates | 29.8 | 0/0 |
| LLM judge over executed result previews (eager) | +4 | 13/8 |
| judge, "switch only if clearly convinced" | worse | 7/4 |
| Claude's result matches a Codex candidate → take it | 33.9 | 5/0 |
| **the two stacked: cross-match first, judge on the rest** | **38.0** | 12/1 |
| pairwise both-orders judge w/ DISTINCT probe (v3) | < eager | over-switches |

Self-consistency voting is dead on arrival here: on the 22 questions where c1
is wrong but some candidate is right, 16 have zero pairwise agreement and 6
agree on the *wrong* answer. Own-candidates are correlated voters. Cross-model
agreement is the opposite: it never broke a correct c1 across two scoring
regimes (5 wins, 0 losses), and as a confidence signal it splits the set into
51%-accurate (confirmed, 51q) vs 16%-accurate (unconfirmed) tiers. Judging
works only as *picking* — consistent with the `RESULTS.md` reviewer negative
result; the fancier pairwise judge found 2 new grain wins but over-switched
and netted below the plain eager one. Selector iteration frozen at the stack;
further variants get tested on fresh seeds only.

Within-model unanimity (all 3 candidates return the same rows) ≈ 73% accurate
on 18% of questions — useless for selection, useful as a confidence rung.
Candidate disagreement is a free ambiguity detector: granting the system one
clarifying question on flagged cases is worth up to +18 (the whole pass@3
band). Untried; needs an interaction protocol.

## For upstream

- Document the case-sensitivity requirement (`lower_case_table_names=1`) or
  regenerate the 12 lowercase golds; scoring is currently platform-dependent.
- dw_real_20/49/57 golds don't execute anywhere (bad column refs).
- ~11 more golds look inconsistent with their question text (fan-out-inflated
  sums, missing/extra columns) — same class the paper's own error analysis
  reports.

## Portability

Prompts now go to the CLIs over stdin with explicit UTF-8 (Windows cmd.exe
caps argv at 8191 chars and defaults pipes to ANSI; both silently fatal for
~30KB prompts with non-ASCII). Binaries resolve via `shutil.which` so bare
`codex`/`claude` work on Windows (`.cmd`) and macOS alike. `CODEX_BIN`/
`CLAUDE_BIN` still override. No behavior change off-Windows.

## Reproduce

```bash
cd eval/myagent
CODEX_SQL_FIX=1 CODEX_SQL_EXPLORE=1 CODEX_STYLE_GUIDE=1 CODEX_SCHEMA_SKILL=1 \
CODEX_N_CANDIDATES=3 CODEX_REASONING_EFFORT=high \
  ./run.sh --dataset dw_real --setting 2 --q_fn dev

cd .. && uv run python evaluate_ex_acc.py --dataset dw_real --multi \
  --input_dir unified-output/myagent/<run_name>
```

DB must be case-insensitive (see above). Third seed + grain-aware
disambiguation (replace rule 3 with profiled fan-out facts; 54 target
questions from the taxonomy) in progress. Selector scripts to land under
`eval/selectors/` once stable.
