# ADR 0002: Deterministic proficiency, grading, gaps and roadmaps
**Decision**: LLMs may generate questions/text but never set scores. Proficiency is a versioned weighted estimator over evidence (`prof-v1`). **Why**: explainability, testability, auditability. **Consequence**: coefficients (kind weights, half-life) are unvalidated heuristics until calibrated on real learner data.
