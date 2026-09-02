# Annotated Reading List — BEAVER dw_real Agentic Text-to-SQL Experiments

Compiled 2026-09-01. Context: our eval runs Codex CLI (GPT-5.6, 3 candidates/question) + claude-opus-5 (1 answer) on BEAVER dw_real (121 questions, MySQL). Established: single-model majority vote recovers nothing; gold-blind LLM judge over result previews 26.4%→30.6%; cross-model result-set agreement is a zero-loss selection signal (51% confirmed vs 15.7% unconfirmed); confirm-then-judge stack 35.5%; ~61% of failures are grain ambiguities (COUNT vs COUNT(DISTINCT), join fan-out); style-guide prompt prior transferred negatively; planned: risk-coverage reporting, grain-aware schema profiling, simulated clarification mode (26.4%→44.6% ceiling).

Verification key: papers below were read at abstract level plus key sections via arXiv unless marked otherwise. Claims marked **[unverified]** come from search snippets or secondhand summaries only.

---

## A. Self-consistency, candidate sampling, and why within-model votes fail

### Wang et al. (2022/2023), "Self-Consistency Improves Chain of Thought Reasoning in Language Models" — arXiv:2203.11171, ICLR 2023
The origin of sample-k-then-majority-vote: sample diverse reasoning paths at nonzero temperature and marginalize by voting on final answers; large gains on math/commonsense reasoning. The implicit assumption is that errors across samples are (partially) independent so the modal answer is more likely correct.
**Steal/apply:** This is the null hypothesis our finding (1) falsifies for our setting: with 3 deliberately *ambiguity-spanning* candidates from one model, votes are correlated by construction and the modal reading is just the model's prior — which is exactly the wrong thing to trust on ambiguous enterprise questions. Cite it as the baseline that breaks; our candidates are closer to "interpretation sampling" than temperature sampling, so voting measures interpretation prior, not correctness.

### Pourreza et al. (2024), "CHASE-SQL: Multi-Path Reasoning and Preference Optimized Candidate Selection in Text-to-SQL" — arXiv:2410.01943, ICLR 2025
Generates candidates via three *structurally different* generators (divide-and-conquer, execution-plan CoT, instance-aware synthetic few-shots), then selects with a fine-tuned binary pairwise selection LLM rather than voting. 73.0% on BIRD test (SOTA at submission).
**Steal/apply:** Two direct lessons: (a) candidate diversity should come from different *generation strategies*, not just different ambiguity readings from one prompt — supports our cross-model result; (b) their selector is pairwise (A-vs-B, both orders), not "pick from a numbered list" — a concrete fix for our judge's candidate-2 preference. Their execution-result grouping before selection is what we already do implicitly with cross-model result-set matching.

### Kohli (2026), "Nine Judges, Two Effective Votes: Correlated Errors Undermine LLM Evaluation Panels" — arXiv:2605.29800
Measures pairwise error correlation across 9 LLM judges: mean φ = 0.391 (Claude×Gemini up to 0.603); Kish effective sample size of the 9-model panel is only n_eff ≈ 2.18, stable at 2.2–2.5 across datasets/temperatures/prompts. Panel accuracy falls 8–22 points below the Condorcet independence prediction; the best single judge matches the panel.
**Steal/apply:** The cleanest published quantification of *why* our within-model vote recovered nothing and why even cross-model agreement is worth less than naive Condorcet math suggests — but note our cross-model signal is agreement on *executed result sets*, a much stricter event than agreement on a label, which is plausibly why it stays zero-loss. Use n_eff / error-correlation φ as a diagnostic to report for our Codex candidates.

### Rosales & Miret (2025), "Diverse LLMs or Diverse Question Interpretations? That is the Ensembling Question" — arXiv:2507.21168
On binary QA (BoolQ, StrategyQA, PubMedQA), compares ensembling different models vs. one model answering multiple self-generated interpretations of the question. Interpretation diversity beats model diversity for majority voting; model ensembles land between best and worst member.
**Steal/apply:** Partially *contradicts* our setup's implicit logic: they find interpretation ensembles help voting — but their tasks have one true answer, whereas our grain-ambiguous questions genuinely admit multiple readings, so voting over readings collapses to the prior. Useful contrast to cite when arguing that for ambiguous inputs the right use of interpretation-diverse candidates is *disagreement detection* (our clarification trigger), not voting.

Also relevant, not separately annotated: CSC-SQL (arXiv:2505.13271) documents the widening self-consistency@k vs pass@k gap in text-to-SQL **[abstract-level only]**; Query and Conquer (arXiv:2503.24364) is the canonical execution-result-equivalence selection method.

---

## B. Cascades, confidence routing, selective prediction

### Chen, Zaharia & Zou (2023), "FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance" — arXiv:2305.05176
LLM cascade: cheap model answers first; a learned scoring function decides whether the answer is reliable or the query escalates to a stronger model. Matches best-single-LLM accuracy at up to 98% lower cost, or +4% accuracy at equal cost.
**Steal/apply:** Our cross-confirmation tier is a two-model cascade run in reverse (we always pay for both). FrugalGPT's framing suggests the deployable version: run Codex first, escalate to Claude *only* when candidates disagree (our ambiguity detector), and report cost-vs-accuracy, not just accuracy. The key component we lack is a calibrated scoring function; their answer is "train a small scorer on (query, answer) → reliability," which our judge approximates zero-shot.

### Liskowski (2026, Snowflake), "Streaming Model Cascades for Semantic SQL" — arXiv:2604.00660
For per-row LLM operators in SQL, routes rows through a cheap proxy and escalates uncertain ones to an oracle model. SUPG-IT gives joint probabilistic precision/recall guarantees by iteratively refining two thresholds on a stream; GAMCAL calibrates proxy scores to true-positive probabilities with a GAM. F1 ≥ 0.95 with up to 58% fewer oracle calls than LOTUS/SUPG.
**Steal/apply:** Different granularity (rows, not queries) but the same statistical machinery we need for risk-coverage: two-threshold accept/escalate with explicit guarantees. The GAMCAL move — calibrate a raw confidence signal into a probability, then threshold — is exactly what to do with our judge scores + confirmed/unconfirmed tier before drawing a risk-coverage curve. Also the closest cultural neighbor to our work (Snowflake, SQL, cascades).

### Richardson (2026), "What Predicts Correctness in Text-to-SQL? A Selective-Prediction Study" — arXiv:2607.06799
Benchmarks confidence signals for text-to-SQL selective prediction: string/structural/execution self-consistency, schema-relevance, executability, logprobs, LLM-judge scores. Black-box self-consistency signals get 0.61–0.68 AUROC; LLM judges 0.72–0.78; a **two-provider ensemble reaches 0.82 AUROC** with calibrated probabilities, and gives usable risk-coverage trade-offs where self-consistency fails.
**Steal/apply:** Independent published replication of both our negative and positive findings: within-model consistency is a weak correctness signal, cross-provider agreement is the strongest one available. Adopt its reporting format wholesale — AUROC per signal + risk-coverage curves — for our planned selective-prediction section, and cite it as convergent evidence for the 51%/15.7% confirmed/unconfirmed split.

### Wen et al. (2024/2025), "Know Your Limits: A Survey of Abstention in Large Language Models" — arXiv:2407.18418, TACL 2025
Survey organizing abstention (refusing to answer) by query/model/human-values perspectives, with a catalog of abstention methods, benchmarks, and metrics across the model lifecycle.
**Steal/apply:** Use as the terminology/metrics source for the clarification-mode writeup: "asking a clarification question" is abstention-with-recourse, and the survey's evaluation metrics (coverage, risk, abstention quality) map our 26.4%→44.6% ceiling into standard vocabulary. Cite instead of re-deriving selective-prediction definitions.

---

## C. LLM-as-judge: position bias and self-preference

### Shi et al. (2024), "Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge" — arXiv:2406.07791, AACL-IJCNLP 2025
150k+ evaluation instances across 15 judges, MTBench/DevBench. Position bias is systematic, judge- and task-dependent, not random; it is strongest when the *quality gap between candidates is small*, and only weakly driven by prompt length. Introduces repetition stability, position consistency, and preference fairness metrics.
**Steal/apply:** Directly explains our observation that the judge picks candidate 2 disproportionately: our candidates are ambiguity readings of the same question — near-equal quality — precisely the regime where position bias dominates. Mitigation to implement: evaluate all candidate orderings (or both orders pairwise, CHASE-SQL-style) and take the position-consistent verdict; report their position-consistency metric for our judge.

### Wataoka, Takahashi & Ri (2024), "Self-Preference Bias in LLM-as-a-Judge" — arXiv:2410.21819, NeurIPS 2024 Safe GenAI Workshop
Defines a quantitative self-preference metric; GPT-4 significantly favors its own outputs, and mechanistically judges assign higher scores to *lower-perplexity* (more familiar) texts, whether or not self-generated.
**Steal/apply:** A caution for our cross-model stack: if we ever let claude-opus-5 judge between its own answer and Codex candidates, expect familiarity-driven inflation of its own SQL style. Current design (judge sees only *executed result previews*, gold-blind) largely neutralizes this — result sets carry no stylistic perplexity signal — which is worth stating explicitly as a design rationale in the writeup.

---

## D. Clarification questions and interactive text-to-SQL

### Ding et al. (2025), "AmbiSQL: Interactive Ambiguity Detection and Resolution for Text-to-SQL" — arXiv:2508.15276 (demo)
Fine-grained ambiguity taxonomy (ambiguities from database elements vs. from LLM reasoning); detects ambiguity, asks *multiple-choice* clarification questions, rewrites the question from answers, feeds a production backend (XiYan-SQL). Reported exact-match on a 40-question ambiguous subset jumps 42.5%→92.5% with clarification **[the 42.5→92.5 figure is from search summaries of the paper; verify against the PDF before citing numerically]**.
**Steal/apply:** Closest system to our planned clarification mode. Steal: (a) multiple-choice questions generated from the *specific detected ambiguity type* rather than free-form "what did you mean"; (b) question-rewriting (not SQL patching) as the resolution mechanism, which keeps the generator unchanged. Their taxonomy should be cross-walked to our grain-ambiguity classes.

### Qiu, Li, Su & Chen (2025), "Interactive Text-to-SQL via Expected Information Gain for Disambiguation" — arXiv:2507.06467
Treats generation as probabilistic inference over a *distribution of candidate SQL queries*; selects the clarification question with maximal expected information gain over that distribution; evaluated with simulated users.
**Steal/apply:** The principled upgrade of our "candidates-disagree ⇒ ask" trigger. We already have the candidate distribution (3 Codex readings + Claude); EIG tells us *what* to ask — the question that best splits the candidates that actually disagree on the result set. Also validates our simulated-clarification evaluation methodology (they simulate users too), useful when defending the 44.6% ceiling as a ceiling, not an achieved number.

### Dong et al. (2024), "PRACTIQ: A Practical Conversational Text-to-SQL dataset with Ambiguous and Unanswerable Queries" — arXiv:2410.11076 **[abstract-level only]**
Conversational dataset with four-turn structure (question → clarification request → user clarification → clarified SQL), covering ambiguous *and unanswerable* categories; SOTA systems struggle on both.
**Steal/apply:** Template for how to score the clarification interaction itself (did the system ask the right thing, did it use the answer correctly) rather than only end accuracy. Also a reminder to add an "unanswerable" bucket: some dw_real misses may be unanswerable-as-asked, which selective prediction should treat differently from ambiguity.

---

## E. Ambiguity in text-to-SQL benchmarks / gold-SQL underdetermination

### Bhaskar, Tomar, Sathe & Sarawagi (2023), "Benchmarking and Improving Text-to-SQL Generation under Ambiguity" (AmbiQT) — arXiv:2310.13659, EMNLP 2023
3000+ examples where one NL question maps to *two* plausible gold SQLs, via lexical and structural ambiguity — including join ambiguity and precomputed-aggregate ambiguity. Shows beam search yields token-level, not logically distinct, diversity; LogicalBeam (plan-based templates + constrained infilling) surfaces both readings up to 2.5× more effectively.
**Steal/apply:** The founding document for our core claim that single-gold benchmarks (BEAVER included) punish defensible readings. Their join/precomputed-aggregate ambiguity categories are the published ancestors of our COUNT vs COUNT(DISTINCT)/fan-out class. Their finding that naive sampling gives token diversity, not reading diversity, retroactively justifies our prompt design (explicitly asking Codex for distinct ambiguity readings).

### Saparina & Lapata (2024), "AMBROSIA: A Benchmark for Parsing Ambiguous Questions into Database Queries" — arXiv:2406.19073, NeurIPS 2024 D&B (Spotlight)
Databases generated from scratch so that scope ambiguity, attachment ambiguity, and vagueness *cannot* be resolved from DB content; each question has 2–3 correct SQLs with NL interpretations. Even top LLMs fail to recognize and enumerate interpretations.
**Steal/apply:** Scope ambiguity ("count of X per Y" vs overall) is exactly our grain problem in linguistic clothing. Use AMBROSIA's interpretation-enumeration metric (did the model produce *all* readings?) as an additional score for our 3-candidate generator — our pass@3=44.6% vs candidate-1=26.4% gap is that metric in disguise.

### Shen et al. (2025), "A Study of In-Context-Learning-Based Text-to-SQL Errors" — arXiv:2501.09310 **[abstract/HTML skim via search only; author list unverified]**
Error taxonomy for ICL text-to-SQL across models/benchmarks; includes a "Wrong COUNT Object" class (COUNT applied to the wrong column/entity) and shows existing repair methods fix a small fraction of errors.
**Steal/apply:** Published error-taxonomy precedent for our 61%-grain-failure analysis; aligning our failure labels with an existing taxonomy makes the number citable and comparable. Their observation that generic self-repair doesn't fix these errors supports our choice of *disambiguation before generation* over post-hoc repair.

---

## F. Schema/data profiling for disambiguation ("grain-aware")

### Huang, Damalapati & Wu (2023), "Aggregation Consistency Errors in Semantic Layers and How to Avoid Them" — arXiv:2307.00417, HILDA@SIGMOD 2023
From the DB (not LLM) literature: join fan-out silently inflates aggregates (double counting); correctness requires knowing the metric's *level of detail* (grain) relative to join keys — the summarizability problem. Proposes per-join-key-group weighting plus human-in-the-loop inspection instead of BI-tool heuristics.
**Steal/apply:** The theoretical backbone for the grain-aware experiment: our COUNT vs COUNT(DISTINCT) failures are textbook summarizability violations. Concretely steal the framing "declare the grain of the measure before aggregating": profile key cardinalities/fan-out per join path (1:1 vs 1:N vs M:N) and inject that as declarative facts into the prompt ("joining orders→items multiplies order rows ~3.2×; COUNT(order_id) over this join double-counts"). Nobody has published this as an LLM prompting technique — this is our novelty claim, and this paper is the citation that defines the problem.

### Talaei et al. (2024), "CHESS: Contextual Harnessing for Efficient SQL Synthesis" — arXiv:2405.16755
Multi-agent pipeline whose Information Retriever profiles database *values* (keyword extraction + LSH + vector index over entities) and whose Schema Selector prunes large schemas adaptively; plus an LLM unit-tester over candidates. Strong BIRD results.
**Steal/apply:** Closest published precedent for putting *data profiles* (not just schemas) in the prompt — but CHESS retrieves value examples for entity matching, not cardinality/fan-out statistics for grain. Positions our experiment as extending profiling from "which literal values exist" to "how keys multiply under joins." Their LLM unit-tester (generate NL assertions, check candidates against them) is also a cheap addition to our judge stage.

---

## G. ReFoRCE (read closely — method details)

### Deng et al. (2025), "ReFoRCE: A Text-to-SQL Agent with Self-Refinement, Consensus Enforcement, and Column Exploration" — arXiv:2502.00675, ICLR 2025 VerifAI Workshop; Spider 2.0 SOTA (35.83 Snow / 36.56 Lite); Snowflake-Labs/ReFoRCE on GitHub
Pipeline: (1) DB-info compression via pattern-based table grouping + LLM schema linking; (2) generate k candidates, each passed through a self-refinement loop on execution feedback (syntax errors *and empty results* trigger refinement, capped at 5 iterations; 407/547 examples need none); (3) **majority vote over execution-equivalent answers with a strict-winner rule**: a candidate is accepted only if its vote count strictly exceeds every other candidate's — *any tie ⇒ the example is classed low-confidence and deferred*, never randomly resolved at this stage; (4) deferred cases go to **iterative column exploration**: up to 10 progressively more complex `SELECT ... LIMIT 20` probe queries (simple → nested), executed sequentially with LLM repair of failing probes and adjustment of related ones, and the resulting observations feed a fresh generation round; leftovers fall back to random pick. Ablations: −3.65 EX without compression, −2.37 EX (Lite) without column exploration (+5.75 EX@8 when applied to *all* examples), −2.01 EX (Snow) without majority voting. Cost: 1.69→3.52 LLM calls/example with exploration.
**Differences from what we built:** (a) their vote equivalence is over *execution results with self-refinement first*, so trivially-broken candidates are repaired before voting — our candidates vote (or rather, fail to) as-generated; (b) their strict-majority-else-defer rule is a *selective-prediction gate*, functionally our candidates-disagree ambiguity detector — but they route deferrals to automated data exploration where we route to a judge / (planned) human clarification; (c) they never use a second model — consensus is within-model, which per our finding (1) and Kohli 2026 should be a weak signal, yet works for them likely because refinement + execution-equivalence classes decorrelate candidates more than raw sampling does; (d) the +5.75 EX@8 from exploring *all* examples says exploration adds recall, not just tie-breaking.
**Steal/apply:** Steal the exact deferral rule (strict winner else defer) and the probe-query recipe (≤10 annotated `SELECT`s, `LIMIT 20`, simple→complex, repair-on-error) as a third tier between our cross-confirmation and clarification: confirmed → accept; unconfirmed → column-explore + regenerate; still ambiguous → ask. Their empty-result-triggers-refinement rule would also have caught some of our silent wrong-grain candidates cheaply.

---

## H. Enterprise text-to-SQL / BEAVER-adjacent evaluation

### Chen et al. (2024/2025), "BEAVER: An Enterprise Benchmark for Text-to-SQL" — arXiv:2409.02038; beaverbench.github.io
First benchmark from *private* data warehouses: 9,128 question-SQL pairs from real query logs, 812 tables, 19 domains (7,978 public, rest held out). Off-the-shelf LLMs with standard prompting/RAG perform poorly; attributed to no pretraining exposure to enterprise schemas, much higher schema complexity, and analyst-style questions requiring multi-table joins and aggregation.
**Steal/apply:** Our substrate. Note for the writeup: the paper frames low scores as capability gaps; our 61% grain-ambiguity analysis suggests a fourth cause the paper doesn't isolate — *gold-SQL underdetermination of analyst questions* — i.e., part of the headroom is not model failure. That reframing (supported by threads D/E) is a publishable contribution on its own.

### Ma et al. (2026), "Can AI Agents Answer Your Data Questions? A Benchmark for Data Agents" (DAB) — arXiv:2603.20576
54 enterprise-grounded queries across 12 datasets, 9 domains, 4 DBMSs, from a formative study across six industries; evaluates full pipelines (integration + transformation + analysis), not just NL→SQL. Best frontier model (Gemini-3-Pro) reaches only 38% pass@1; includes failure-mode annotation of agent trajectories.
**Steal/apply:** Confirms the enterprise gap persists for full agentic pipelines, and their trajectory failure-mode annotation scheme is a model for how to present our error analysis. Note: a claim circulating in search results that an agentic framework scores "10.8% on BEAVER with GPT-5.2, 30.1% with oracle hints" could **not be traced to this paper's abstract — [unverified, secondhand; likely from another 2026 paper (possibly BADGER, arXiv:2606.02109, unread)]**. Do not cite that number without locating the primary source.

### (2026), "Both Ends Count! Just How Good are LLM Agents at Text-to-'Big SQL'?" — arXiv:2602.21480 **[search-snippet level only; authors unverified]**
Evaluates LLM agents on large-scale analytical SQL; error analysis reports "Unaligned Aggregation Structure" errors: 44.16% mixing MAX and COUNT, 15.80% spurious MAX, 5.41% spurious COUNT.
**Steal/apply:** Independent evidence that aggregation-structure confusion dominates errors on analyst-style SQL, corroborating our 61% grain figure from a different benchmark. Read the full paper before citing; if its taxonomy distinguishes COUNT vs COUNT(DISTINCT), it is the closest published comparison point for our failure breakdown.

---

## Cross-cutting synthesis (what the literature says about our seven findings)

1. **Within-model vote fails** — predicted by Kohli 2026 (n_eff ≈ 2 even across models) and the CSC-SQL consistency-vs-pass gap; Wang et al.'s assumptions don't hold for interpretation-diverse candidates (Rosales & Miret nuance).
2. **Judge on result previews +4.2pts** — consistent with Richardson 2026 (judge AUROC 0.72–0.78 beats consistency signals); gold-blind result-preview design also dodges self-preference bias (Wataoka 2024).
3. **Cross-model agreement zero-loss** — Richardson 2026's two-provider ensemble (0.82 AUROC) is the direct published analog.
4. **Stacking 35.5%** — an instance of FrugalGPT-style cascading; report cost curves.
5. **61% grain failures** — named and formalized as summarizability (Huang 2023); corroborated by AmbiQT's join/aggregate ambiguity classes and Both Ends Count's aggregation-error stats.
6. **Negative style-guide transfer** — no single paper found on convention-prior transfer from synthetic→real questions; nearest neighbors are benchmark-overfitting critiques inside BEAVER/AMBROSIA. Possibly a novel observation worth a paragraph.
7. **Clarification ceiling 44.6%** — evaluation methodology validated by EIG-2025 (simulated users) and PRACTIQ (conversation-turn scoring); AmbiSQL shows very large realized gains are plausible, not just ceilings.
