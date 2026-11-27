# ADR 0009: Golden-set strategy and numeric quality targets

**Status:** accepted · **Resolves:** TASKLIST I4.T4 and I6.T6 targets, and "who supplies the golden examples"

## Context
We must prove that automatic scoring and retrieval are trustworthy. Human-rated examples are scarce and slow; the only available model is weak as a judge (D3), so LLM labels cannot be the ground truth.

## Options for supplying golden examples
| Source | Strengths | Weaknesses |
|---|---|---|
| Owner supplies everything | high trust | bottleneck, no scale |
| Expert reviewers rate in the Review Studio | real human judgement, doubles as production workflow | slow, needs rater time |
| **Synthetic fault injection (mutation)** on reviewed good content | unlimited, known ground truth, tests *sensitivity* | measures defect detection, not holistic quality |
| Open-licensed reference material | free positives, rubric anchors | licence checks; not our content style |
| LLM-labelled data | cheap | circular and weak under D3 — **not allowed as ground truth** |
| Production reviewer decisions and learner outcomes | grows with use; real | arrives late |

## Decision: a layered "golden set factory"
1. **Mutation set (automatic, from day one).** Take reviewed, high-quality atoms and apply deterministic fault injectors: corrupt Mermaid, break LaTeX, remove a required contract element, delete or fabricate a citation, swap a formula term, insert a contradiction from a curated fact table, add duplicates, degrade readability. Every mutant has a **known label** (which dimension must drop; critical or not). Used to measure detection recall and regression sensitivity.
2. **Expert-rated set (human, via the Review Studio).** Reviewers (admin/reviewer roles, D1) rate artifacts on the rubric, **blind**, with **30% double-rated** to measure inter-rater agreement. **Active-learning sampling** prioritises artifacts where scorers are uncertain or disagree. Start with a 40-artifact pilot to unblock calibration, then grow to the target size. Rater onboarding uses 12 **anchor examples** with written rationales.
3. **Open-licensed reference excerpts** (licence-checked) as positive anchors for readability and grounding.
4. **Production by-product:** every approve/reject with reasons in the Review Studio is appended to the registry with provenance; learner item statistics and feedback adjust priorities, not labels.
5. **Hygiene:** golden sets are **versioned and immutable**; split into *calibration* (30%) and a *sealed holdout* (70%) that is never used for tuning; **no LLM-only labels** (LLMs may draft candidate rationales for humans to accept or change).
**Who supplies them:** the system supplies mutation sets and sampling; the owner nominates at least two human raters (initially the owner and one reviewer is acceptable); AI may prepare candidates but never decides labels. Fallback if only one rater exists: reduce double-rating to a rotating 20% with an external spot check, and mark agreement statistics as provisional.

## Rater panel (brainstormed decision: who rates)

Different quality dimensions need different kinds of rater. One person cannot credibly judge a distributed-systems deep dive *and* whether a beginner finds it clear.

| Rater type | Rates | Why | Size |
|---|---|---|---|
| **Rubric owner / adjudicator** (the product owner) | all dimensions; resolves disagreements; signs off anchors | single source of rubric authority; avoids drift | 1 |
| **Domain reviewers** (colleagues with real experience in Python, ML/GenAI, system design/distributed systems, cloud, databases) | technical correctness, depth, practical implementation, maths | correctness needs domain judgement; one part-time reviewer per domain, shared across related domains | 2–4 |
| **Target-level learners** (beginner/intermediate users of the platform) | readability, clarity, whether the example helped | a learner-experience signal that experts cannot give; **never** used for correctness | 3–5 |
| **Mathematics checker** (only for formula-heavy items) | mathematical correctness | formulas are where plausible-looking errors hide | 1 (as needed) |

**Rules:** raters never rate content they authored or approved; ratings are blind to scorer output and to each other; disagreement of two or more bands goes to the adjudicator; a rater must first rate the 12 anchor examples and reach weighted κ ≥ 0.60 against the reference before joining; every rating records the rater, time spent and a one-line rationale.

**Effort estimate:** about 12 minutes per artifact. 150 artifacts plus 30% double-rating is roughly 195 ratings, or about 40 rater-hours. Spread over 4 raters that is about 10 hours each over 2–3 weeks. The 40-artifact pilot is about 9 hours in total.

**Minimum viable panel (to start):** the owner plus one domain reviewer. Learner raters and extra domain reviewers join as the set grows. This is not needed until Iteration 6; Iteration 0–5 use the mutation sets only.

**Mapping to the app:** reviewers use the `reviewer` role (D1); learner raters use a restricted `rater` permission that can only rate readability and clarity.

## Numeric targets (initial; revisited only with evidence, never relaxed because a model changes)
**Golden set size:** at least 150 content artifacts across the 7 benchmark domains, at least 20 per decision band (reject / revise / improve / publish), human inter-rater quadratic-weighted κ ≥ 0.70 (if humans disagree more than this, fix the rubric before judging the scorer).

| Area | Metric | Target |
|---|---|---|
| Scorer vs humans | Spearman ρ per dimension (deterministic-driven dimensions) | ≥ 0.70 |
| | Quadratic-weighted κ on decision bands | ≥ 0.65 overall |
| | Mean absolute error of the 0–100 overall score | ≤ 8 points |
| | **Critical-failure detection recall** | ≥ 0.95 (precision ≥ 0.80) |
| | Deterministic validators vs fixtures | 100% |
| | LLM-judged dimensions (advisory, 8B model) | ρ ≥ 0.55, otherwise **weight 0** in the composite until met |
| | Assessment item key agreement | ≥ 0.90 |
| Retrieval (≥ 150 labelled queries) | Recall@5 / Recall@10 | ≥ 0.90 / ≥ 0.95 |
| | MRR | ≥ 0.75 |
| | nDCG@10 | ≥ 0.80 |
| | Cross-domain contamination in top-3 | ≤ 2% of queries |
| | Retrieval latency p95 (50k atoms, excluding embedding) | ≤ 50 ms |
| Regression | Drop in ρ or κ versus the previous accepted run | fails CI if > 0.03 |

**Statistical rule:** a target counts as met only if the **lower bound of the 95% bootstrap confidence interval** on the sealed holdout meets it.

## Consequences
The Review Studio is on the critical path for calibration (I6.T8 feeds I6.T6); early iterations rely on mutation sets; initial numbers are provisional until the human set reaches size.
