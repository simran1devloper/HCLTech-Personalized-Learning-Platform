# ADR 0005: In-memory repositories for new domains (temporary)
**Decision**: ship domain logic + API behind repository protocols with in-memory stores; SQL schema delivered and verified separately. **Why**: validate behaviour/contracts first within one session. **Consequence**: new-domain learner data is lost on restart; SQL repositories are the top remaining task.
