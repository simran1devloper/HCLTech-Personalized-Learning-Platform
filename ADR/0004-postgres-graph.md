# ADR 0004: Skill graph in PostgreSQL + in-process traversal
**Decision**: edges in `skill_dependencies` (cycle trigger), traversal in Python (Kahn/DFS). **Why**: graph is small (hundreds of nodes); Neo4j not justified. Revisit if traversal latency is measured as a bottleneck.
