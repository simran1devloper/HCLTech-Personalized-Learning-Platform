# ADR 0008: Retrieval — embeddings with metadata pre-filtering; provider-agnostic; pgvector in production

**Status:** accepted · **Resolves:** TASKLIST I4.T4

## Context
Atom retrieval must find compatible content by *meaning* (concept, objective, level) quickly, with structured filters (type, level band, language, status). The owner chose **embeddings**. Constraints: a single weak hosted LLM (D3), privacy (learner personal data stays local; course content may go remote), SQLite for dev and PostgreSQL for production.

## Decision
1. **Embedding provider interface** (like the LLM provider interface) with adapters: **local `fastembed` (ONNX, no GPU/torch) — default**, Ollama `/api/embed`, OpenAI-compatible, Gemini. Each stored vector records `embedding_model`, `dim` and `embedding_version`; changing the model triggers re-embedding, and queries only compare vectors from the same model.
2. **Default model:** `BAAI/bge-small-en-v1.5` (384 dimensions) via fastembed, chosen for size and speed on CPU. *Not assumed best:* in Iteration 4 we **A/B candidate models on the labelled retrieval set and pick by measured recall** (candidates: bge-small, all-MiniLM-L6-v2, `nomic-embed-text` via Ollama if the host serves it). Licences are verified at adoption.
3. **Storage:** PostgreSQL **pgvector** with an HNSW index (tests via the `pgvector/pgvector` Docker image); SQLite stores vectors as BLOBs and uses brute-force cosine with NumPy (adequate for tens of thousands of atoms).
4. **Query pipeline:** structured **pre-filter** (concept subtree, type, level band, language, status = published) → vector similarity → threshold and top-k. A **lexical re-rank/fallback** (PostgreSQL full-text) is available behind a flag for exact-term queries but is not the primary mechanism.
5. **What is embedded:** `title + objective + short text` of each atom (atoms are already small, so no chunking). Query embeddings are cached.
6. **Privacy:** only course content is embedded; learner data never is. Remote embedding providers are opt-in.

## Evidence and caveats
- `uv pip install --dry-run fastembed` resolves 13 packages for Python 3.12 (includes ONNX runtime); the first run downloads the model (network needed once, or bundle it).
- The hosted Ollama's last successful model listing showed **no dedicated embedding model** (a chat model can embed but poorly and slowly). The host was unreachable when re-checked for this ADR, so its embedding support is **unverified**; the local default avoids depending on it.

## Consequences
Adds `fastembed` (and `numpy`, `pgvector` client) dependencies; requires a re-embedding job and model-versioning discipline; recall is validated against numeric targets (ADR 0009).
