# ADR 0007: LaTeX validation — layered, deterministic (policy lint + authoritative KaTeX parse)

**Status:** accepted · **Resolves:** TASKLIST I1.T5 (LaTeX)

## Context
Math blocks must render in the frontend with **KaTeX**. We need server-side validation that is deterministic (no LLM), fast, and consistent with what the browser will render. KaTeX options and policies matter: with `trust: false`, KaTeX still *accepts* `\href{javascript:…}` and `\def…` (it does not throw), so "KaTeX parses it" is not the same as "it is safe and allowed".

## Options considered
1. **Pure-Python parser/linter only** (balance, environments, allow-listed commands) — no runtime dependency, but can drift from KaTeX and reject or accept differently.
2. **Python libraries** (`latex2mathml`, `pylatexenc`) — different grammar and coverage than KaTeX; false rejections/acceptances.
3. **KaTeX via Node only** — authoritative for rendering, but does not enforce our content policy and needs Node in the worker image.
4. **Browser-side check only** — not server-side, not available to workers.
5. **Layered: policy lint (Python) + authoritative KaTeX parse (Node)** — **chosen**.

## Decision
- **Layer 1 — policy lint (pure Python, always on):** length and nesting limits; delimiter and environment pairing; a **forbidden-command list** (`\href`, `\url`, `\includegraphics`, `\input`, `\include`, `\write`, `\def`, `\gdef`, `\newcommand`, `\renewcommand`, `\csname`, `\catcode`, `\expandafter`, `\html*` and similar); a **supported-command allow-list generated from the installed KaTeX version** by a script and committed, with a CI check that it is current.
- **Layer 2 — authoritative parse (Node, in the worker path, not the API request path):** `katex.renderToString(tex, {throwOnError: true, strict: 'error', trust: false, maxExpand: 200})` run by a **persistent child process** speaking JSON lines, with timeouts and memory limits, batching per block. Reuses the `katex` package already in the repo.
- **Degradation:** if Node is unavailable, Layer 1 runs alone and the result is flagged `partial_validation` (never reported as fully validated).
- Findings are structured (`code`, `severity`, `position`, `message`). Policy violations are **critical**; syntax errors are **errors**.
- **Mathematical correctness** (is the formula true) is a separate sub-metric, not part of syntax validation (candidate: symbolic checks later).

## Evidence (measured here, KaTeX 0.19.1)
Rejected deterministically: unbalanced braces, unknown commands, mismatched environments (`matrix` vs `pmatrix`), malformed superscripts. Accepted: fractions, matrices, summations. **Accepted but unsafe/disallowed:** `\href{javascript:…}` and `\def` — hence Layer 1. Cost: about **0.36 ms** per expression in-process when warm; about 115 ms Node cold start, which the persistent process amortises.

## Consequences
Deterministic and reproducible; consistent with the renderer; adds a Node runtime to worker images; the allow-list must be regenerated when KaTeX is upgraded (CI enforces it).
