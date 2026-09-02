# myagent + claudeagent on BEAVER `dw_real`: results

Same protocol as `RESULTS.md`, on the real-query split: 121 questions from
actual MIT warehouse query logs (`dw_real/dev.json`, the seed corpus the
synthetic sets were expanded from). Codex CLI = `gpt-5.6-sol` via ChatGPT
login; Claude CLI = `claude-opus-5`. Everything gold-blind.

Scoring gotcha first, because it moves every number: 15/121 gold queries
depend on case-insensitive identifiers: 12 reference lowercase table names
(`dw.employee_directory`, ...) and 3 (dw_real_20/49/57) refer to a table alias
in a different case than it was declared. MySQL on macOS is case-insensitive
and runs them all; a default Linux server errors them out, and the scorer then
treats gold as empty. Numbers below are from a rebuilt DB with
`--lower-case-table-names=1` (= macOS-comparable). On a strict-case server
everything drops 2 to 4 points from gold-side failures alone.

## Scoreboard (`dw_real`, full 121-question `dev`, high effort)

| Config | cand-1 | pass@3 |
|--------|:------:|:------:|
| setting 1, one-shot, no techniques (control) | 29.8% | n/a |
| setting 1, full stack | 26.4% | 43.8% |
| setting 1, claudeagent, explore+fix | 29.8% | n/a |
| setting 1, **Concur** (full stack + selector) | **29.8%** | n/a |
| setting 2, one-shot, no techniques (control) | 35.5% | n/a |
| setting 2, full stack (3 seeds) | 29.8 / 28.1 / 29.8 | 47.9 / 46.3 / 47.9 |
| setting 2, claudeagent, explore+fix | 30.6% | n/a |
| setting 2, cross-model selection only | 33.9% | n/a |
| setting 2, **Concur** (full stack + selector) | **38.0%** | n/a |
| setting 2, bare model + 3 candidates only (no techniques) | 35.5% | 41.3% |
| setting 2, Concur on those bare-model candidates | 33.9% | n/a |
| setting 2, full stack with the grain rule (rule 3 replaced by profiled fan-out facts) | 35.5% | 48.8% |
| setting 2, Concur on the grain-rule candidates | 33.9% | n/a |

At setting 1 the selector adds 3.3 points to the generator (26.4 to 29.8) and
lands level with the control; cross-model concurrence fired on 67 questions
there versus 51 at setting 2. At both settings the plain model beats the
generator's first candidate (setting 1 by 3.3, setting 2 by 5.7).

Full stack = the `dw` best config (fix + explore + style guide + schema skill +
3 candidates). Three independent seeds span 1.7 pts on both metrics (seeds 1
and 3 identical to the decimal), so run-to-run noise is small; deltas ≥3 pts
are real. Seed 3 note: dw_real_82 was generated with the DB loops off: its
explore pass produced a query whose post-timeout fetchall OOM-killed the
process three times (a `timed()` daemon-thread can cap wall-clock but not
memory; bounded fetch is the proper fix, TODO in agent_common). Candidate match histogram
36/18/4, so candidate 2 carries 2× the weight it did on `dw` (18 vs 9); real
questions are more ambiguous than the synthetic ones.

## Is the generator overkill? No: its value is the candidate set.

Bare GPT-5.6 asked for three candidates, with none of the techniques, gets
35.5 / 41.3: candidate 1 identical to the one-query control, and only 7 more
questions reachable through candidates 2 and 3 (histogram 43/5/2). The full
stack's histogram is 36/18/4: a worse first guess, but 22 questions reachable
through the alternatives. Run Concur's selector on the bare candidates and it
loses ground, 35.5 to 33.9 (2 wins, 4 losses), because there is almost nothing
for it to find. On the full stack's candidates it gains 8.2 (29.8 to 38.0).
The exploration, repair, and prompt priors do not make the first answer
better; they make the alternatives worth selecting from, and that is where
the pipeline's margin over the plain model comes from.

## The control beats the stack's candidate 1. The style guide is why.

35.5 vs 29.8: the no-technique control wins by ~6. Taxonomy of all 89
candidate-1 misses (per-question dossiers: question + hints + gold + all
candidates + executed rows): ~61% involve COUNT vs COUNT(DISTINCT) or
fan-out-inflated aggregates. `dw_real` gold dedups by default; style-guide
rule 3 says the opposite ("no DISTINCT unless the question says unique") and
was fit on `dw`, where it gained +3. Here it systematically points candidate 1
the wrong way. The house prior doesn't transfer from synthetic to real. It
anti-transfers.

But the pipeline still wins end-to-end: 3 candidates + a gold-blind selector →
38.0, above the control's 35.5. Candidate 1 loses; the bracket + selection
recovers more than rule 3 costs. The techniques' value routes through
selection, not through the first guess.

Taxonomy totals: underdetermined 40, gold-suspect 26 (15 = identifier-case
failures, 14 semantic, with some overlap in categorization), model-error 20, evaluator-artifact 2,
hint-backfire 1. On `dw` hint-backfire was 10/65; on real questions the
decomposition hints almost never contradict the final question.

## Selectors (frozen setting-2 generations)

| Policy | acc | wins/losses vs c1 |
|--------|:---:|:---:|
| candidate 1 always | 29.8 | n/a |
| majority vote over own 3 candidates | 29.8 | 0/0 |
| LLM judge over executed result previews (eager) | +4 | 13/8 |
| judge, "switch only if clearly convinced" | worse | 7/4 |
| Claude's result matches a Codex candidate → take it | 33.9 | 5/0 |
| **the two stacked: cross-match first, judge on the rest** | **38.0** | 12/2 |
| pairwise both-orders judge w/ DISTINCT probe (v3) | < eager | over-switches |

Self-consistency voting is dead on arrival here: on the 22 questions where c1
is wrong but some candidate is right, 16 have zero pairwise agreement and 6
agree on the *wrong* answer. Own-candidates are correlated voters. Cross-model
agreement is the opposite: it never broke a correct c1 across two scoring
regimes (5 wins, 0 losses), and as a confidence signal it splits the set into
57%-accurate (confirmed, 51q) vs 17%-accurate (unconfirmed) tiers. Judging
works only as *picking*, consistent with the `RESULTS.md` reviewer negative
result; the fancier pairwise judge found 2 new grain wins but over-switched
and netted below the plain eager one. Selector iteration frozen at the stack;
further variants get tested on fresh seeds only.

Within-model unanimity (all 3 candidates return the same rows) is 77% accurate
on 18% of questions: useless for selection, useful as a confidence rung.
Candidate disagreement is a free ambiguity detector: granting the system one
clarifying question on flagged cases is worth +22 questions, 29.8 to 47.9 (the
whole pass@3 band), at the cost of asking on 82% of queries. Untried as an
interaction; the numbers are the simulated ceiling (`selectors/cascade_tiers.py`).

## The grain rule: rule 3 replaced by profiled fan-out facts (all 121)

Rule 3 of the style guide ("no DISTINCT unless the question says unique") was
fit on `dw` and points candidate 1 the wrong way on `dw_real` (previous
section). `CODEX_GRAIN=1` replaces that one rule with facts profiled from the
data per question (`myagent/grain_profile.py`): which hinted joins multiply
rows, which counted columns therefore repeat, which measures get inflated.
The other eight rules stay. Run on the full 121 with the full stack, one seed.

| Generator, setting 2 | cand-1 | pass@3 |
|-----|:------:|:------:|
| full stack, frozen seed 1 | 29.8 | 47.9 |
| full stack, grain rule | 35.5 | 48.8 |
| Concur on the frozen candidates | 38.0 | n/a |
| Concur on the grain-rule candidates | 33.9 | n/a |

The rule does what it was built to do: candidate 1 rises 5.7 points to
exactly the plain-model control (35.5), coverage gains one question. Then the
selector loses it back. On the grain candidates Concur scores 33.9, below the
generator's own candidate 1 (7 wins, 9 losses) and below the 38.0 it reaches
on the frozen candidates (4 wins, 9 losses head to head). Cross-model
concurrence fired on 42 questions instead of 51, so more questions went to the
judge, and the judge switched away from a correct candidate 1 on 12 of the 43
it had. The pattern is the same as on the bare-model candidates (also 33.9):
once candidate 1 is already good, the selector has less to find and more to
break. Selection only pays when the first guess is weak and the alternatives
are strong, which is exactly the frozen generator's shape (36/18/4).

So the grain rule is a real generator improvement and a real selector
regression, and the two cancel. It stays out of the method for now. The next
experiment is a selector that trusts candidate 1 more when the generator's
own prior is data-derived, or a concurrence-only stack with no judge.

Two earlier subset results are superseded by the table above and kept only in
the experiment log: the 54 flagged questions alone (grain rule 9/20 versus
seeds 0-1/16-17; no style guide 8/11), and the three gold-against-its-own-data
regressions (dw_real_106, 52, 11) found there, which still hold.

## For upstream

- Document the case-sensitivity requirement (`lower_case_table_names=1`) or
  regenerate the 15 case-dependent golds (12 lowercase table names, 3 alias
  case mismatches: dw_real_20/49/57); scoring is currently platform-dependent.
- 14 golds look inconsistent with their question text (fan-out-inflated sums,
  e.g. dw_real_77 reports building E37's area as 13,135,200 = 218,920 x 60
  joined rows; requested columns omitted, e.g. dw_real_44 groups by course name
  but does not return it), the same class the paper's own error analysis
  reports. Two verified by hand; the rest categorized from dossiers.

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

DB must be case-insensitive (see above). Every number above maps to a command
in `eval/selectors/RUNBOOK_dw_real.md`; the selector, cascade, audit, and
dossier scripts live in `eval/selectors/`. Grain-aware disambiguation
(`CODEX_GRAIN=1`, `myagent/grain_profile.py`) replaces rule 3 with profiled
fan-out facts; profile first with `python grain_profile.py --q_fn dev`.
