"""All seed questions keyed by canonical skill name. Add a skill by adding a list here; no application logic changes.

``concept`` is the coarse *area* that is measured and reported in skill-gap analysis (3-6 per skill, so a 10-question attempt gives most areas two or more
questions); ``subtopic`` keeps the fine-grained item. ``GROUPS`` maps the fine-grained names used while authoring onto the coarse concepts."""
from app.domains.assessments.bank.seed_core import AGENTIC, FASTAPI, ML, PYTHON, SQL
from app.domains.assessments.bank.seed_more import ALL_MORE

GROUPS: dict[str, dict[str, str]] = {
    "Python": {"Data types": "Data types and collections", "Mutability and references": "Data types and collections", "Collections": "Data types and collections",
               "Comprehensions": "Data types and collections", "Functions": "Functions, decorators and generators", "Decorators": "Functions, decorators and generators",
               "Generators": "Functions, decorators and generators", "OOP": "Object-oriented programming", "Exception handling": "Exception handling",
               "Async programming": "Async and concurrency"},
    "SQL": {"Filtering": "Querying and filtering", "NULL handling": "Querying and filtering", "Grouping": "Querying and filtering", "Aggregation": "Querying and filtering",
            "Joins": "Joins and subqueries", "CTEs": "Joins and subqueries", "Window functions": "Joins and subqueries", "Indexes": "Performance and indexing",
            "Query optimization": "Performance and indexing", "Transactions": "Transactions and design", "Normalization": "Transactions and design"},
    "Machine Learning": {"Overfitting": "Generalization", "Bias-variance trade-off": "Generalization", "Regularization": "Generalization", "Cross-validation": "Generalization",
                         "Evaluation metrics": "Evaluation and data hygiene", "Classification metrics": "Evaluation and data hygiene", "Data leakage": "Evaluation and data hygiene",
                         "Model selection": "Evaluation and data hygiene", "Gradient descent": "Training and models", "Feature engineering": "Training and models",
                         "Clustering": "Training and models", "Ensemble methods": "Training and models"},
    "Agentic AI": {"Agent loop": "Agent fundamentals", "Tool use": "Agent fundamentals", "Planning": "Agent fundamentals", "Orchestration": "Agent fundamentals",
                   "Memory": "Memory and reflection", "Reflection": "Memory and reflection", "Guardrails": "Safety and reliability", "Prompt injection": "Safety and reliability",
                   "Failure modes": "Safety and reliability", "Evaluation": "Evaluation and cost", "Cost control": "Evaluation and cost"},
    "FastAPI": {"Path and query parameters": "Requests and validation", "Pydantic validation": "Requests and validation", "Response models": "Requests and validation",
                "Error handling": "Requests and validation", "Dependency injection": "Dependencies and structure", "Routers": "Dependencies and structure",
                "Lifespan events": "Dependencies and structure", "Async vs sync": "Async and background work", "Background tasks": "Async and background work",
                "Security": "Security and testing", "Testing": "Security and testing"},
    "REST API Design": {"Resource modeling": "Resources and methods", "HTTP methods": "Resources and methods", "Idempotency": "Resources and methods",
                        "Status codes": "Responses, errors and security", "Error handling": "Responses, errors and security", "Security": "Responses, errors and security",
                        "Pagination": "Scaling and evolution", "Versioning": "Scaling and evolution", "Caching": "Scaling and evolution", "Rate limiting": "Scaling and evolution"},
    "Data Structures and Algorithms": {"Complexity analysis": "Complexity and recursion", "Recursion": "Complexity and recursion", "Dynamic programming": "Complexity and recursion",
                                       "Hash tables": "Linear structures and patterns", "Stacks and queues": "Linear structures and patterns", "Two pointers": "Linear structures and patterns",
                                       "Trees": "Sorting, trees and graphs", "Graphs": "Sorting, trees and graphs", "Heaps": "Sorting, trees and graphs", "Sorting": "Sorting, trees and graphs"},
    "System Design": {"Scalability": "Scaling and traffic", "Load balancing": "Scaling and traffic", "CDN": "Scaling and traffic", "Rate limiting": "Scaling and traffic",
                      "Capacity estimation": "Scaling and traffic", "Caching": "Caching and data", "Sharding": "Caching and data", "Replication": "Caching and data",
                      "CAP theorem": "Consistency and messaging", "Consistency models": "Consistency and messaging", "Message queues": "Consistency and messaging"},
    "RAG": {"RAG basics": "Indexing and embeddings", "Chunking": "Indexing and embeddings", "Embeddings": "Indexing and embeddings", "Index freshness": "Indexing and embeddings",
            "Vector search": "Retrieval quality", "Hybrid search": "Retrieval quality", "Reranking": "Retrieval quality", "Metadata filtering": "Retrieval quality",
            "Grounding": "Generation and evaluation", "Evaluation": "Generation and evaluation", "Context window": "Generation and evaluation", "Security": "Generation and evaluation"},
    "LangGraph": {"StateGraph basics": "Graph building", "Edges": "Graph building", "Compilation": "Graph building", "Tool nodes": "Graph building",
                  "State reducers": "State and persistence", "Checkpointing": "State and persistence", "Threads": "State and persistence", "Human-in-the-loop": "State and persistence",
                  "Cycles": "Control flow and runtime", "Streaming": "Control flow and runtime", "Failure handling": "Control flow and runtime"},
    "MCP": {"Architecture": "Architecture and protocol", "Protocol": "Architecture and protocol", "Transports": "Architecture and protocol", "Capabilities": "Architecture and protocol",
            "Local vs remote": "Architecture and protocol", "Primitives": "Primitives and tools", "Tool design": "Primitives and tools", "Discovery": "Primitives and tools",
            "Sampling": "Primitives and tools", "Security": "Security"},
    "PostgreSQL": {"MVCC": "Storage and concurrency", "Vacuum": "Storage and concurrency", "Transactions": "Storage and concurrency", "Locking": "Storage and concurrency",
                   "WAL": "Storage and concurrency", "Index types": "Indexing and performance", "EXPLAIN": "Indexing and performance", "Partial indexes": "Indexing and performance",
                   "Connection pooling": "Indexing and performance", "JSONB": "Data modeling", "Constraints": "Data modeling"},
    "Redis": {"Data structures": "Data and expiry", "Expiration": "Data and expiry", "Eviction": "Data and expiry", "Architecture": "Data and expiry",
              "Persistence": "Durability and replication", "Replication": "Durability and replication", "Pub/Sub": "Durability and replication",
              "Atomicity": "Caching and concurrency", "Caching patterns": "Caching and concurrency", "Cache stampede": "Caching and concurrency", "Locks": "Caching and concurrency",
              "Transactions": "Caching and concurrency"},
    "Docker": {"Concepts": "Images and builds", "Layers and caching": "Images and builds", "CMD vs ENTRYPOINT": "Images and builds", "Multi-stage builds": "Images and builds",
               ".dockerignore": "Images and builds", "Image tags": "Images and builds", "Volumes": "Runtime and networking", "Networking": "Runtime and networking",
               "Compose": "Runtime and networking", "Restart policies": "Runtime and networking", "Security": "Security and secrets", "Secrets": "Security and secrets"},
    "Distributed Systems": {"CAP theorem": "Consistency and consensus", "Consensus": "Consistency and consensus", "Quorums": "Consistency and consensus",
                            "Leader election": "Consistency and consensus", "Consistency models": "Consistency and consensus", "Replication": "Replication and partitioning",
                            "Partitioning": "Replication and partitioning", "Transactions": "Replication and partitioning", "Idempotency": "Failures and delivery",
                            "Time and ordering": "Failures and delivery", "Failure detection": "Failures and delivery", "Delivery semantics": "Failures and delivery"},
}


def _grouped(skill: str, items: list[dict]) -> list[dict]:
    g = GROUPS.get(skill, {})
    return [{**q, "subtopic": q["concept"], "concept": g.get(q["concept"], q["concept"])} for q in items]


_RAW = {"Python": PYTHON, "SQL": SQL, "Machine Learning": ML, "Agentic AI": AGENTIC, "FastAPI": FASTAPI, **ALL_MORE}
SEED: dict[str, list[dict]] = {skill: _grouped(skill, items) for skill, items in _RAW.items()}
