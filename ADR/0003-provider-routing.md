# ADR 0003: Capability-gated provider routing
**Decision**: hard requirements (structured output, local-only, context) filter providers; failover only within the eligible set; non-recoverable errors are never retried. **Why**: never silently violate privacy/format guarantees.
