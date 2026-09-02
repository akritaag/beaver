# Experiment log: agentic text-to-SQL on BEAVER dw_real

A record of what we believed, what we tried, what broke, and what we learned,
in the order it happened. Written to be mined for a paper later, so the
failures are kept in and the reasoning at each fork is spelled out. Numbers
are from `RESULTS_dw_real.md`; this file is about why.

## 0. Starting position

Ankit's branch had a Codex-CLI agent (`myagent`) and a Claude-CLI agent
(`claudeagent`) evaluated on BEAVER's synthetic `dw` split, with a documented
best config (self-fix, explore/verify, a house-style guide, a schema-profile
"skill", three candidate answers per question) reaching 39% single-answer and
49% pass@3 on 100 sampled questions. Our goal: reproduce that on a fresh
machine, extend it to the real-query split (`dw_real`, 121 questions drawn
from actual MIT query logs, the seed corpus the synthetic set was grown from),
and find out whether the improvement over the paper's baselines is real, and
what part of it is ours versus the model's.

Two framing decisions made early that shaped everything after:

1. We treated the paper's Setting 2 (all five oracle subtask hints supplied:
   tables, join keys, column mapping, domain knowledge, decomposition) as the
   evaluation contract, because Ankit's harness implements exactly that
   protocol and it is what his 39/49 means. We accepted from the start that
   every number we would report is a with-hints number, not a "type a question,
   get an answer" number, and that the paper's honest cold number (~10%) is a
   different quantity.
2. We decided nothing we built could see gold. The agent runs its own queries
   and reads its own result rows, never the reference SQL or the reference
   result. This turned out to matter for more than integrity: the single
   cleanest explanation of what worked and what failed is whether a component
   respected that line (see section 6).

## 1. Reproduction, and the first number that lied

The infrastructure work is in the commit messages; the part that belongs in a
paper is this. The first real run "succeeded" with exit code zero and produced
121 prediction files, and every one of them was empty. A Windows encoding
default had corrupted the prompts and the CLI rejected them. The harness
logged the failures per question and carried on. If we had scored that run
without looking at the files we would have reported an accuracy near zero and
believed it. Lesson we kept applying: a completed run is a claim, not a
result. Every run since has been checked for empty predictions before
scoring, and the two later scoring bugs we caught (a missing `--multi` flag
that treated three candidates as one query; a database that failed a tenth of
the gold queries) were caught the same way, by refusing to trust a summary
number until the files behind it had been opened.

## 2. The first dw_real number, and the reflex to compare it

Full stack on dw_real: 26.4% single-answer, 44.6% pass@3 (later corrected to
29.8 / 47.9, see section 8). The reflex was to compare it to Ankit's 39/49 and
to the paper's 25.9% (ReFoRCE, Setting 2). We wrote down why neither
comparison was apples to apples before letting ourselves draw a conclusion:
different question set (real vs synthetic; 121 vs 100 vs the paper's full
9,128), different model generation (the paper's strongest was GPT-5.2; ours
was GPT-5.6), and the paper's Setting 2 figures are averages over seven
models, several of them weak. Any one of these can move a number by more than
the gap we were looking at.

That list produced the experiment we consider the keystone of the whole
project: a model-matched control. Same model, same reasoning effort, same
hints, same one-shot example in the prompt, and none of the agent techniques.
Whatever the gap between that and the full stack, that gap is the technique's,
and nothing else's.

## 3. The 18-point band, and the first idea for closing it

The gap between single-answer accuracy and pass@3 was about 18 points. Those
are questions where the model wrote a correct query and ranked it second or
third. pass@3 is not deployable (you must serve one answer and you have no
answer key), so the question became: can a gold-blind rule pick the right
candidate?

Our first instinct was the standard one from the self-consistency literature:
execute all three candidates, and if two return the same result set, trust
the agreement. Majority vote. The intuition behind it deserves to be stated
in its strongest form because it is a good intuition: there is one right
answer and many possible wrong ones, so two independent attempts that agree
are far more likely to have both landed on the truth than to have both landed
on the same mistake. Truth repeats; lies scatter.

Majority vote recovered zero questions. Not a small number, zero. The
selector never once overrode candidate 1, and when we looked at the 22
questions in the band, 16 had no agreement between any pair of candidates and
in the other 6 the agreeing pair was wrong and the lone dissenter was right.

## 4. Why the intuition failed: interpretation errors versus computation errors

This is the discussion we most want to preserve. The "truth repeats, lies
scatter" argument is correct for one kind of error and wrong for another,
and enterprise text-to-SQL is dominated by the second kind.

A computation error is a wrong join key, a typo in a filter literal, a
mis-specified aggregation. Two independent attempts that make computation
errors land in different places in a huge space of possible result sets, so
agreement between them is real evidence. This is why executing candidates and
comparing result sets is a meaningful signal at all, and it is the regime the
self-consistency literature was developed in.

An interpretation error is different. "How many courses did each department
offer?" has two defensible readings (count rows, or count distinct courses)
and the question does not say which. Wrongness is not spread across a large
space; it is concentrated on the single most natural misreading, and every
strong model shares the same sense of what is natural. So when the reference
answer took the less natural reading, both attempts pick the same wrong one,
and they agree. Agreement is not evidence of truth here; it is evidence of a
shared prior.

Our candidates made this worse by construction. Candidates 2 and 3 are
deliberately generated by flipping the choices the model is least sure of,
so on exactly the questions where candidate 1 is wrong, the candidates
disagree by design (the 16), or they share the wrong reading on one axis while
flipping an inert one (the 6; in one case the "flip" was adding a redundant
column to a GROUP BY, which produces identical rows). Ankit's earlier dossiers
had already measured this from the other side: in 46 of 65 failures, Codex
and Claude wrote the identical non-gold interpretation. Convergent wrongness
is the normal case, not the exception.

The general statement we would put in a paper: self-consistency voting
measures the model's interpretation prior, not correctness, whenever the
residual errors are interpretive rather than computational. On BEAVER,
Ankit's taxonomy put interpretive failures at roughly 70% of misses; ours on
dw_real put grain ambiguity alone at 61%. Voting cannot work in this regime
and it did not.

## 5. What did work for selection: reading, and diversity

Two things recovered questions, and they recovered them for opposite reasons.

The first was an LLM judge that reads the executed result sets. Given the
question, the three candidates, and a preview of what each returned (never
gold), a separate model picks one. It gained 13 questions and lost 8, net +5
(about 4 points). It works because it can use signals that a result-set
equality check cannot: a result that is empty when the question clearly has
answers, a row count that is absurd for the entity being counted, a column
shape that does not match what was asked. It can also side with a lone
correct candidate against a wrong agreeing pair, which voting is structurally
unable to do. It did exactly that on three of the six convergent-wrongness
cases.

The second was cross-model agreement. We ran Claude independently on all 121
questions and asked whether Claude's executed result matched any of Codex's
three candidates. It did on 51 questions, and every time that match pointed
away from candidate 1 (nine times), switching was either a win or free; it
never broke a correct answer. Five wins, zero losses. This works for the
reason voting failed: a different model with different training is a
partially independent draw of the interpretation prior, where three samples
from one model are not. The published version of this observation exists
(correlated-error panels have an effective sample size near two regardless of
panel size; two-provider ensembles score highest as confidence signals on
text-to-SQL), and our result is a clean instance of it.

Stacked in confidence order (cross-model confirmation where it fires, judge
on the rest) they reached 35.5%, later 38.0% on corrected scoring, converting
about half the band. The two signals are complementary: the cross-model check
is nearly infallible but fires on fewer than half the questions; the judge
fires everywhere but errs, and the cross-model layer happened to shield seven
of its eight losses.

## 6. Pick, never edit

Ankit had tried something close to the judge before we arrived and it hurt
every time: a reviewer that probes candidates with its own queries and
rewrites them on contradicting evidence, measured at minus 2 to minus 7
across four variants. His post-mortem is the sharpest thing in the repository
and we want it in the paper. A gold-blind reviewer can only test whether a
query obeys the question. But at the margin where the reviewer acts, the
remaining scoring gap is between question-faithful readings and the reference
author's reading, and those point in opposite directions: the reviewer finds
a candidate that "contradicts the question", corrects it, and destroys a match
that was correct precisely because it mirrored the reference's unstated
choice. Meanwhile the errors a probe could legitimately fix were already fixed
upstream by the explore-and-repair loop, so the reviewer had nothing left but
false positives.

His closing line was that any revival should be restricted to picking among
candidates, never editing them. Our judge is that revival and it worked. We
then rebuilt the judge with every improvement the literature suggested
(pairwise comparison in both orders to remove position bias, result-set diffs,
a probe that detects when two candidates differ only by DISTINCT) and it did
worse than the plain version: it found two new wins the simple judge never
found, proving the probe carries signal, but it over-switched and lost six.
Same lesson from a different angle: the more agency you give a gold-blind
component at the margin, the more it optimizes question-faithfulness against
you.

## 7. The control, and the day the stack lost

The model-matched control came back at 33.1% single-answer (35.5% corrected).
The full stack's candidate 1 was 26.4% (29.8% corrected). The no-technique
baseline beat the technique stack by six points. This was the outcome we had
named in advance as the nightmare ("what if the model was the only
improvement?") and it is the most useful thing that happened.

The failure taxonomy, done Ankit's way over all 89 candidate-1 misses with
per-question dossiers, said why in one line repeated fifty times: candidate 1
used plain COUNT, gold used COUNT(DISTINCT), over a join that multiplies rows.
About 61% of misses involved this grain issue. And the reason candidate 1
chose plain COUNT is that the house-style guide told it to: rule 3, "no
DISTINCT unless the question says unique", written after failure analysis on
the synthetic `dw` set, where it gained three points and cracked questions no
run had solved. Real MIT analysts write dedup-by-default SQL. The convention
prior fit on synthetic questions did not merely fail to transfer; it
anti-transferred, and it did so on the single most common pattern in the real
data.

Two things we take from this. The specific one: a "house style" learned from
one question population is a bet that the next population shares it, and
the bet lost here, which is an argument for deriving the DISTINCT decision
from the data (does this join path multiply the counted key?) rather than
from a rule. That is the grain experiment, still to run. The general one:
the stack still won end-to-end, 38.0 against the control's 35.5, because
bracketing the ambiguity into three candidates and selecting among them
recovers more than the bad prior costs. The techniques' value did not route
through the first guess at all. It routed through selection. A paper that
reported candidate-1 accuracy alone would have concluded the techniques were
harmful, and been wrong about the pipeline while being right about the prior.

## 8. The benchmark was measuring the platform

Reading the dossiers, an agent flagged golds that did not execute. An audit
found 15 of 121: twelve referenced tables in lowercase (`dw.employee_directory`)
and three referred to a table alias in a different case than declared; MySQL
on macOS resolves all of them and a default Linux server does not. (We first
read the three alias cases as broken column references; re-running them on
the rebuilt database showed they execute fine, which is its own small lesson
about checking a claim on the fixed system before repeating it.) The scorer treats a failed gold
as an empty result, so a prediction "matched" those twelve by also erroring.
Every number we had was therefore a strict-case number, while anything scored
on a Mac, including presumably the paper's own, was lenient-case. We rebuilt
the database case-insensitive and re-scored everything; every figure rose two
to four points and every conclusion survived. The finding goes upstream
regardless: the benchmark's score depends on the operating system it is scored
on, and that is a reproducibility bug in the benchmark, not in any method.

Same audit, second finding: hint-backfire, which was 15% of Ankit's misses on
synthetic questions (decomposition sub-questions contradicting the final
question), was 1 of 89 on real questions. Real questions are single-intent;
the template-composed synthetic ones stack sub-questions that can disagree.
This is quiet evidence for the benchmark's own design choice to seed from
real logs.

## 9. Variance, and the discipline of freezing

Everything in sections 3 through 7 was tuned on the same 121 questions from
the same generation run, eight selector variants deep. That is post-hoc
selection and we said so before running the check. Three independent
generations of the full stack scored 29.8 / 28.1 / 29.8 single-answer and
47.9 / 46.3 / 47.9 pass@3, a spread of 1.7 points, with seeds one and three
identical to the decimal. The selector policy was frozen after variant eight
and its first out-of-sample test was the corrected re-score, where it rose
rather than fell. Every delta we have discussed above three points is real
against this noise floor. Any further selector idea gets tested on the fresh
seeds, not the tuning seed.

## 10. Deployment reframing: confidence, abstention, and asking

The selection work produced something we did not set out to build: a
confidence signal. Cross-model agreement splits the questions into a 57%
accurate tier and a 17% accurate tier; within-model unanimity (all three
candidates returning identical rows) marks an 18% slice that is 77% accurate.
This turns the benchmark's all-or-nothing accuracy into a risk-coverage curve:
answer only the confident tier at 57% precision, or answer everything at 38%.

It also gave a mechanical ambiguity detector: candidates disagreeing. The
question we keep returning to is that a real analyst would not guess between
"rows" and "distinct rooms"; they would ask. The benchmark cannot answer back,
but it can bound the value of asking. If the system may pose one clarifying
question on flagged cases, and the user's answer resolves exactly the axis the
candidates disagree on, the ceiling is pass@3 on those cases: 29.8 to 47.9, about 18 points
for one question. It asks on 82% of
queries in the naive version, which is too chatty, and tuning the trigger is
open work. But the shape of the claim is unusual and we think publishable:
on real enterprise questions, one clarifying exchange is worth more than
every prompt technique combined.

## 10b. Convicting rule 3, and what replaces it

Two runs on the 54 questions the taxonomy flagged, keeping the full stack in
both. First, the style guide removed entirely: first-candidate accuracy went
from 0 of 54 (all three seeds) to 8, and pass@3 fell from 17 to 11. So the
rule was the damage, and the other eight rules were doing real work on
coverage that we would have thrown away with them. Second, rule 3 alone
replaced by a data-derived rule: profile each question's hinted joins for row
multiplication, tell the model which counted columns get duplicated and which
measures get inflated, and let it decide DISTINCT from that. First-candidate
accuracy 9 of 54, pass@3 20, better than the seeds on both, with no other
rule touched. The facts visibly changed what the model wrote: on the first 17
questions checked, 15 first candidates switched from plain COUNT to
COUNT(DISTINCT).

The three questions the grain rule lost are the ones worth remembering. On
one, the profile found no duplicate room keys, the rule said plain COUNT, and
gold used DISTINCT anyway. On another, the profile found multiplication, the
rule said DISTINCT, and gold wanted plain counts. On the third, the advice to
aggregate a measure at its own grain made the model pre-aggregate a SUM that
gold computes over the fan-out. In all three the reference query disagrees
with what its own data implies. No rule derived from the data can reach those,
and the honest write-up says so: the data-derived rule fixes the cases where
the reference author was consistent with the data, and loses the cases where
they were not. The measure advice should be demoted from an instruction to a
fact.

What is still open is the full split: a rule that helps the 54 flagged
questions could hurt the other 67, and until those run the grain rule has a
subset result, not a row in the table.

## 10c. Is the whole apparatus overkill?

The fair version of the question: plain GPT-5.6 with the same hints scores
35.5 on one query. Concur scores 38.0 with two models and about ten calls per
question. Three questions of gain, inside noise, at ten times the cost. So we
ran the minimal pipeline: bare GPT-5.6 asked for three candidates and nothing
else, then the same selector. If it reached 38, the generator's techniques
were decoration.

It reached 33.9, below its own first candidate. The bare model's alternatives
rarely hold the right answer (5 plus 2 questions beyond candidate 1, against
18 plus 4 for the full stack), so selection has nothing to recover and only
its mistakes remain. The techniques we had been calling net negative are net
negative for the first answer and net positive for the set: they spread the
three candidates across the readings the question allows, which is exactly
what a selector needs. The honest summary is not "the techniques hurt" or
"the techniques help" but "the techniques move value from the first guess to
the alternatives, and a selector is what turns that into accuracy."

## 11. Things that did not work, kept honestly

- Majority vote over own candidates: zero gain (section 4).
- Conservative judge prompt: cut losses less than it cut wins, net worse.
- Pairwise both-orders probe judge: real probe signal, over-switching, net
  worse than the naive judge.
- The synthetic-fit style guide on real queries: net negative on candidate 1.
- Editing reviewers (Ankit, four variants): all negative.
- Two runs killed silently by an unbounded fetch after a query timeout on one
  question; the timeout wraps `fetchall` in a thread it cannot stop. The fix
  (bounded fetch) is a harness change, not a research one, but it cost a day.

## 12. What we can claim, and what we cannot

Can: a faithful reproduction of the paper's Setting-2 protocol; a
model-matched control that isolates technique from model; a three-seed noise
floor; a gold-blind selection stack that lifts deployable accuracy on real
queries from 29.8 to 38.0, above both the control and the paper's best
published Setting-2 figure; a mechanistic account of why self-consistency
fails and cross-model agreement succeeds in interpretation-dominated regimes;
a negative-transfer result for convention priors; a platform-dependence bug in
the benchmark's scoring; and a clarification-value bound.

Cannot, yet: that the techniques beat the paper's baselines on the paper's
own full question set (we ran one warehouse's real split); that the gap to
the paper's 25.9% is technique rather than model, except through our own
control (the paper's baselines on GPT-5.6 have not been run); that the grain
hypothesis fixes the rule-3 damage (untested); that the selector stack holds
on the other warehouses.

## 13. Next

Grain-aware disambiguation on the 54 flagged questions, replacing rule 3
with profiled fan-out facts. The minus-style-guide ablation, to put a number
on rule 3's cost in isolation. Refreshed confidence tiers on the corrected
database. A clarification protocol with a tuned trigger. Then the same
questions on nova and neutron, if the leaderboard requires them.
