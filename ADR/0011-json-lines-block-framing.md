# ADR 0011: JSON Lines block framing for progressive generation

**Status:** accepted (decision N3/D8) · **Context:** today a JSON section is parsed only after the full response; one malformed character fails the whole section. Small models also struggle to emit one large valid JSON document.

## Decision
The model emits **one block per line** (JSON Lines). Each line is parsed, schema-validated and persisted independently, then announced as `block.completed`. Deterministic repair (trim, close fences, normalise) comes first; at most one bounded LLM repair; then only the failed block regenerates.

## Alternatives rejected
A partial-JSON streaming parser (extra dependency and edge cases) and whole-section JSON (current behaviour).

## Consequences
A malformed block never blocks later blocks; replay and de-duplication use stable block IDs; the pure parser is built and tested in Iteration 1 (I1.T8) before any streaming depends on it.
