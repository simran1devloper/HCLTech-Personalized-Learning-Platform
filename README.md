# HCLTech Personalized Learning Platform

**LearnAI** measures what a learner actually knows, models how skills depend on one another, and builds a learning roadmap that updates as evidence accumulates.

Proficiency, skill gaps, career readiness and roadmaps are computed **deterministically from assessment evidence**. Large language models (LLMs) are used for tutoring and content generation; they never assign scores.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-6-3178C6?logo=typescript&logoColor=white)

---

## Table of contents

- [Features](#features)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Testing](#testing)
- [API overview](#api-overview)
- [Security](#security)
- [Known limitations](#known-limitations)

---

## Features

### Learning intelligence

| Capability                           | Description                                                                                                                                                                                                                                                                                                      |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Skill graph**                | A seeded catalogue of technical skills with aliases and prerequisite relationships, stored as a validated directed acyclic graph.                                                                                                                                                                                |
| **Evidence-based proficiency** | A 0–5 scale per skill with a confidence score, recency decay, difficulty weighting, snapshot history and a per-evidence explanation (`prof-v1`).                                                                                                                                                              |
| **Adaptive assessments**       | Multiple choice, multiple select, true/false, short answer, scenario, system design, practical, coding and debugging questions. Difficulty adapts to the learner and answer keys never reach the browser.                                                                                                        |
| **Skill-gap analysis**         | Explainable, prerequisite-aware prioritization with a recommended next skill.                                                                                                                                                                                                                                    |
| **Career readiness**           | Ten target roles: AI Engineer, Machine Learning Engineer, Python Backend Engineer, Data Engineer, Full Stack AI Engineer, Full Stack Developer, Software Engineer, DevOps Engineer, Cloud Engineer and Cybersecurity Engineer. Readiness is an evidence-based coverage score, **not** a hiring prediction. |
| **Adaptive roadmap**           | Versioned and incrementally re-planned. Completed work is preserved, mastered skills are skipped only with sufficient evidence, and milestone completion is enforced by the backend.                                                                                                                             |

### AI tutor and study content

- **Streaming AI tutor** with Markdown, syntax-highlighted code, Mermaid diagrams and KaTeX math. Conversations are saved, and context is drawn only from the learner's own data.
- **Diagnostic study flow:** a topic quiz, level classification, study material, flashcards and recommendations.
- **Observable generation pipeline:** a live view of each step (plan, generate, validate, retry, save). Failed sections can be regenerated individually.
- **Content history:** search, archive, restore, duplicate and version study sessions.
- **Safe rendering:** raw HTML is never rendered, and typed content blocks are schema-validated before display.

### Application experience

- Dashboard, skill explorer, interactive skill graph, roadmap and assessment workspaces, and analytics.
- Command palette (`Ctrl+K`), light and dark themes, and keyboard navigation.
- Automated WCAG 2.2 AA accessibility scans in the end-to-end test suite.
- Metrics without a data source are shown as **"Not available"** rather than estimated.

### Platform

- Provider-independent LLM layer supporting **Ollama**, **Google Gemini**, **OpenRouter** and **OpenAI-compatible** APIs, with retries, circuit breakers, failover and privacy gating.
- **SQLite** by default, with optional **PostgreSQL** through SQLAlchemy and Alembic migrations.

---

## Architecture

```mermaid
flowchart LR
    UI["Vue 3 web app<br/>(web/)"] -- "REST + Server-Sent Events" --> API["FastAPI modular monolith<br/>(backend/)"]
    API --> CORE["Deterministic engines<br/>proficiency · gaps · roadmaps"]
    API --> AI["LLM router<br/>retries · failover · privacy"]
    CORE --> DB[("SQLite or PostgreSQL")]
    API --> DB
    AI --> LLM["Ollama · Gemini · OpenRouter<br/>OpenAI-compatible"]
```

### Project structure

```text
.
├── backend/                  FastAPI service
│   ├── app/
│   │   ├── api/v1/           Versioned REST API
│   │   ├── domains/          Business logic (assessments, careers, tutoring, ...)
│   │   ├── ai/               LLM providers, routing and resilience
│   │   ├── agents/           LangGraph workflows
│   │   └── infrastructure/   Database and cache
│   ├── routes/ services/     Authentication and diagnostic-study API
│   ├── alembic/              Database migrations
│   ├── benchmarks/           Performance benchmarks
│   └── tests/                Unit, integration, contract and security tests
├── web/                      Vue 3 + TypeScript single-page application
│   ├── src/                  Pages, features, components and stores
│   └── e2e/                  Playwright end-to-end tests
├── contracts/                Test vectors shared by backend and web
├── ADR/                      Architecture Decision Records
└── docker-compose.dev.yml    PostgreSQL and Redis for local development
```

Key design decisions are recorded in [ADR/](ADR/).

---

## Technology stack

| Layer                               | Technologies                                                                     |
| ----------------------------------- | -------------------------------------------------------------------------------- |
| **Backend**                   | Python, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, LangGraph, HTTPX            |
| **Frontend**                  | Vue 3, TypeScript, Vite, Tailwind CSS 4, Pinia, TanStack Query, VeeValidate, Zod |
| **Visualization and content** | ECharts, Vue Flow, Monaco Editor, Shiki, Mermaid, KaTeX                          |
| **Data**                      | SQLite, PostgreSQL (optional)                                                    |
| **Testing**                   | pytest, Vitest, Vue Test Utils, Playwright, axe-core                             |

---

## Getting started

### Prerequisites

| Requirement  | Version                                                                                                                           |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| Python       | 3.12 (recommended)                                                                                                                |
| Node.js      | 22.22.2+, 24.15+ or 26+                                                                                                           |
| LLM provider | At least one of: a local [Ollama](https://ollama.com) server, or an API key for Gemini, OpenRouter or an OpenAI-compatible service |
| Docker       | Optional, for local PostgreSQL and Redis                                                                                          |

Commands below are for Linux and macOS and start from the repository root.

### 1. Clone the repository

```bash
git clone <repository-url>
cd <repository-folder>
```

### 2. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and set at least a secret key and one LLM provider (see [Configuration](#configuration)). Generate a secret key with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

> **Important:** never commit `.env`. It is listed in `.gitignore`.

### 3. Start the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p data                      # holds the local SQLite database
python -m uvicorn main:app --reload --port 8000
```

| Service              | URL                          |
| -------------------- | ---------------------------- |
| API                  | http://localhost:8000        |
| Interactive API docs | http://localhost:8000/docs   |
| Health check         | http://localhost:8000/health |

Database tables and seed data are created automatically.

### 4. Start the web app

In a second terminal:

```bash
cd web
npm ci
npm run dev
```

Open http://localhost:5173, create an account, set a career goal on the dashboard and take your first assessment.

### Production build

```bash
cd web
npm run build                      # type check, then bundle into web/dist
```

Serve `web/dist` with a static web server, and route unknown paths to `index.html`. Route `/api/`, `/auth/` and `/learning/` to the backend. If the API is on a different origin instead, set `VITE_API_BASE_URL` before building and add the web app's origin to `ALLOWED_ORIGINS` on the backend.

### Deploy to Vercel

[vercel.json](vercel.json) deploys the web app and the API as two [services](https://vercel.com/docs/services) on one domain. The browser calls the API on the same origin, so no service bindings or `VITE_API_BASE_URL` are needed.

1. Import the repository into Vercel with the root directory set to the repository root.
2. Add the project environment variables: `ENVIRONMENT=production`, a new `SECRET_KEY`, `STORE_BACKEND=postgres`, `DB_BACKEND=sqlite`, `DATABASE_URL` (see [Supabase](#supabase)), `LLM_PROVIDERS`, the provider's API key and model, and `ALLOW_UNSIGNED_QUIZ=false`.
3. Run `python scripts/check_supabase.py` from `backend/` once before the first deployment, so tables and seed data already exist.
4. Deploy. To try the setup locally, run `vercel dev` from the repository root.

The `regions` setting should match the database region (`icn1` is Seoul). On serverless hosting the cache and rate limits are per instance, and long or background generation can be cut off by function time limits.

---

## Configuration

The backend reads `.env` from the repository root or from `backend/`. [.env.example](.env.example) documents the available settings.

### Core settings

| Variable                  | Purpose                                                                               | Example                       |
| ------------------------- | ------------------------------------------------------------------------------------- | ----------------------------- |
| `ENVIRONMENT`           | `development` or `production`                                                     | `development`               |
| `SECRET_KEY`            | Signs authentication tokens (32+ characters)                                          | *generated value*           |
| `DB_BACKEND`            | Database used by authentication and study routes: `sqlite` or `supabase` (legacy) | `sqlite`                    |
| `DATABASE_PATH`         | SQLite file, relative to `backend/`                                                 | `data/app.db`               |
| `LLM_PROVIDERS`         | Providers in priority order; later ones are fallbacks                                 | `ollama,gemini`             |
| `LLM_LOCAL_ONLY`        | When `true`, learner data is never sent to a remote provider                        | `false`                     |
| `ALLOWED_ORIGINS`       | Permitted CORS origins (JSON list)                                                    | `["http://localhost:5173"]` |
| `RATE_LIMIT_PER_MINUTE` | API rate limit                                                                        | `60`                        |

### LLM providers

| Provider                             | Settings                                                                                                                           |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Local Ollama**               | `LLM_PROVIDERS=ollamaOLLAMA_BASE_URL=http://localhost:11434``OLLAMA_MODEL=llama3.1:8b` (first run `ollama pull llama3.1:8b`) |
| **Hosted Ollama (Basic auth)** | `LLM_PROVIDERS=ollamaOLLAMA_BASE_URL=https://<host>``OLLAMA_BASIC_AUTH=<user>:<password>OLLAMA_MODEL=<model>`                  |
| **Ollama Cloud**               | `LLM_PROVIDERS=ollamaOLLAMA_BASE_URL=https://ollama.com``OLLAMA_API_KEY=<key>OLLAMA_MODEL=<model>`                             |
| **Google Gemini**              | `LLM_PROVIDERS=geminiGEMINI_API_KEY=<key>``GEMINI_MODEL=gemini-2.5-flash-lite`                                                 |
| **OpenRouter**                 | `LLM_PROVIDERS=openrouterOPENROUTER_API_KEY=<key>``OPENROUTER_MODEL=openai/gpt-4o-mini`                                        |
| **OpenAI-compatible**          | `LLM_PROVIDERS=openaiOPENAI_BASE_URL=<url>``OPENAI_API_KEY=<key>OPENAI_MODEL=<model>`                                          |

To verify an OpenRouter setup, run `python scripts/check_openrouter.py` from `backend/`.

Any non-local host, including a tunnel to your own machine, is treated as a remote provider.

### PostgreSQL (optional)

```bash
docker compose -f docker-compose.dev.yml up -d
```

Then set the following in `.env`:

```ini
STORE_BACKEND=postgres
DATABASE_URL=postgresql+psycopg://learnai:learnai_dev_only@127.0.0.1:5433/learnai
```

These credentials are for **local development only**.

### Supabase

The app uses Supabase as a PostgreSQL database. It does not need the Supabase project URL or API keys for this.

1. In the Supabase dashboard, open **Connect → Session pooler** and copy the connection string.
2. Set the following in `.env`. Change the prefix to `postgresql+psycopg://` and URL-encode special characters in the password.

   ```ini
   DB_BACKEND=sqlite          # follows STORE_BACKEND
   STORE_BACKEND=postgres
   DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres
   ```

3. Verify the setup from `backend/`:

   ```bash
   python scripts/check_supabase.py
   ```

   The check creates or upgrades the tables, confirms that Row Level Security is on, then registers, uses and removes a temporary user.

Migrations enable Row Level Security on every application table without adding policies, so the publishable key cannot read them. Existing SQLite data is not copied to Supabase.

---

## Testing

### Backend

```bash
cd backend
source .venv/bin/activate
pip install pytest pytest-asyncio aiosqlite fakeredis
python -m pytest -o asyncio_mode=auto
```

Tests that need PostgreSQL or Redis are skipped unless those services are running (see `docker-compose.dev.yml`).

### Web

Run these from `web/`.

| Suite              | Command                                        | Scope                                                          |
| ------------------ | ---------------------------------------------- | -------------------------------------------------------------- |
| Unit and component | `npm test`                                   | Stores, stream parsing, Markdown safety, charts and components |
| Type check         | `npm run typecheck`                          | `vue-tsc`                                                    |
| End to end         | `npx playwright install chromiumnpm run e2e` | Full user journeys and accessibility scans                     |

The end-to-end suite starts its own backend (using `backend/.venv`), a temporary SQLite database and a simulated model server, so no real LLM is required.

---

## API overview

| Area                  | Base path                                                              | Purpose                                                       |
| --------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------- |
| Authentication        | `/auth`                                                              | Registration and login                                        |
| Diagnostic study      | `/learning`, `/api/v1/diagnostic-study`                            | Quizzes, study content and learning history                   |
| Skills and careers    | `/api/v1/skills`, `/api/v1/careers`, `/api/v1/goals`             | Skill catalogue, skill graph, readiness and goals             |
| Assessments           | `/api/v1/assessments`                                                | Create, answer, submit and review assessments                 |
| Learning intelligence | `/api/v1/proficiency`, `/api/v1/skill-gaps`, `/api/v1/analytics` | Proficiency, gaps and analytics                               |
| Roadmaps              | `/api/v1/roadmaps`                                                   | Generate, re-plan and complete milestones                     |
| Content history       | `/api/v1/content-sessions`                                           | Saved study sessions, sections and versions                   |
| AI tutor              | `/api/v1/tutor`                                                      | Streaming chat, conversations, feedback and confirmed actions |

The complete, interactive reference is available at http://localhost:8000/docs while the backend is running.

`/api/v1` errors use a consistent envelope:

```json
{ "error": { "code": "...", "message": "...", "details": {} } }
```

---

## Security

- JWT authentication with per-user data isolation.
- Rate limiting, SSRF-safe URL validation and sanitized error responses.
- Quiz answers are graded from a server-signed copy.
- With `ENVIRONMENT=production`, the API refuses to start if `SECRET_KEY` is shorter than 32 characters or left at the built-in default.

### Production checklist

- [ ] Set a unique, randomly generated `SECRET_KEY`. The sample value in `.env.example` is public and is **not** rejected automatically.
- [ ] Set `ENVIRONMENT=production`.
- [ ] Set `ALLOW_UNSIGNED_QUIZ=false`.
- [ ] Restrict `ALLOWED_ORIGINS` to your own domains.
- [ ] Serve all traffic over HTTPS.

Please report vulnerabilities privately to the maintainers rather than opening a public issue.

---

## Known limitations

| Area                       | Limitation                                                                                                                                                |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Test setup**     | The test tools are not listed in a requirements file, and async tests need `-o asyncio_mode=auto`. |
| **Docker**           | `docker-compose.yml` is outdated: it refers to a `frontend/` folder and an `nginx.conf` that are not in the repository. Use the manual setup above. |
| **Generation speed** | Quiz and study-material generation depend on model throughput and can take several minutes on slow or shared hardware.                                    |
| **Code execution**   | No sandbox is configured for learner code, so coding answers that require execution are not scored.                                                       |
| **Sessions**         | The authentication token is stored in `localStorage`; HttpOnly cookies would be stronger.                                                               |
