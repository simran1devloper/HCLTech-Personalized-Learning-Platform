# ADR 0001: Modular monolith alongside legacy modules
**Decision**: add `backend/app/` with domain boundaries inside the existing FastAPI process; keep legacy flat modules to preserve contracts. **Why**: no microservice need; avoids a risky big-bang move. **Consequence**: two config modules (`config.py`, `app/core/config.py`) until legacy is retired.
