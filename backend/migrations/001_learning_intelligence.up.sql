-- 001_learning_intelligence.up.sql
-- ADDITIVE migration: creates new tables only; never drops or rewrites users / learning_history.
-- STATUS: written against PostgreSQL 15 / Supabase; NOT yet executed against a live database in this repo's CI.
BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------- security fix for the existing schema -------------------------------------------------------
-- init.sql created policies with USING (true): any anon/authenticated PostgREST client could read every
-- users row including password_hash. The backend uses the service-role key (bypasses RLS), so
-- deny-by-default is safe for it.
DROP POLICY IF EXISTS "Users can view own record"    ON public.users;
DROP POLICY IF EXISTS "Users can update own record"  ON public.users;
DROP POLICY IF EXISTS "Users can view own history"   ON public.learning_history;
DROP POLICY IF EXISTS "Users can insert own history" ON public.learning_history;
-- RLS stays ENABLED with no permissive policy => no access for anon/authenticated roles.
REVOKE ALL ON public.users, public.learning_history FROM anon, authenticated;

-- ---------- helper ------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END $$;

-- ---------- learners ----------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.learner_profiles (
  user_id UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  hours_per_week NUMERIC(5,1) NOT NULL DEFAULT 5 CHECK (hours_per_week > 0 AND hours_per_week <= 80),
  preferences JSONB NOT NULL DEFAULT '{}',
  language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en','hinglish')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());

-- ---------- skill registry ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.skills (
  skill_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'general', domain TEXT NOT NULL DEFAULT 'software',
  difficulty SMALLINT NOT NULL DEFAULT 2 CHECK (difficulty BETWEEN 1 AND 5),
  estimated_hours NUMERIC(6,1) NOT NULL DEFAULT 10 CHECK (estimated_hours > 0),
  aliases TEXT[] NOT NULL DEFAULT '{}',
  parent_skill_id UUID REFERENCES public.skills(skill_id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','deprecated','proposed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_skills_category ON public.skills(category, status);
CREATE INDEX IF NOT EXISTS idx_skills_aliases ON public.skills USING GIN (aliases);
DROP TRIGGER IF EXISTS trg_skills_touch ON public.skills;
CREATE TRIGGER trg_skills_touch BEFORE UPDATE ON public.skills FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

CREATE TABLE IF NOT EXISTS public.skill_dependencies (
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id) ON DELETE CASCADE,
  prerequisite_id UUID NOT NULL REFERENCES public.skills(skill_id) ON DELETE CASCADE,
  min_proficiency NUMERIC(3,1) NOT NULL DEFAULT 2 CHECK (min_proficiency BETWEEN 0 AND 5),
  PRIMARY KEY (skill_id, prerequisite_id),
  CHECK (skill_id <> prerequisite_id));
CREATE INDEX IF NOT EXISTS idx_skill_deps_prereq ON public.skill_dependencies(prerequisite_id);

-- DB-level cycle guard (defence in depth; the application also checks before insert).
CREATE OR REPLACE FUNCTION public.reject_dependency_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
    WITH RECURSIVE up(id) AS (
      SELECT NEW.prerequisite_id
      UNION
      SELECT d.prerequisite_id FROM public.skill_dependencies d JOIN up ON d.skill_id = up.id)
    SELECT 1 FROM up WHERE id = NEW.skill_id) THEN
    RAISE EXCEPTION 'dependency cycle: % requires % transitively', NEW.prerequisite_id, NEW.skill_id USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_skill_dep_acyclic ON public.skill_dependencies;
CREATE TRIGGER trg_skill_dep_acyclic BEFORE INSERT OR UPDATE ON public.skill_dependencies
  FOR EACH ROW EXECUTE FUNCTION public.reject_dependency_cycle();

-- ---------- careers -----------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.career_roles (
  role_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS public.career_skill_requirements (
  role_id UUID NOT NULL REFERENCES public.career_roles(role_id) ON DELETE CASCADE,
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id) ON DELETE CASCADE,
  target_level NUMERIC(3,1) NOT NULL CHECK (target_level BETWEEN 0 AND 5),
  importance NUMERIC(3,2) NOT NULL DEFAULT 1 CHECK (importance BETWEEN 0 AND 1),
  critical BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (role_id, skill_id));

CREATE TABLE IF NOT EXISTS public.learning_goals (
  goal_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role_id UUID NOT NULL REFERENCES public.career_roles(role_id),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','achieved','abandoned')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_goals_user ON public.learning_goals(user_id, status);

-- ---------- evidence & proficiency --------------------------------------------------------------------
-- Evidence is append-only. The current proficiency is DERIVED (deterministic, versioned) and not stored
-- redundantly; proficiency_snapshots preserves history for audit/trend and is the only stored derivative.
CREATE TABLE IF NOT EXISTS public.skill_evidence (
  evidence_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id),
  kind TEXT NOT NULL CHECK (kind IN ('quiz','exercise','coding','project','self_report')),
  score NUMERIC(5,4) NOT NULL CHECK (score BETWEEN 0 AND 1),
  difficulty NUMERIC(3,1) NOT NULL CHECK (difficulty BETWEEN 0 AND 5),
  n_items INT NOT NULL DEFAULT 1 CHECK (n_items >= 1),
  source_id UUID, occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_evidence_user_skill ON public.skill_evidence(user_id, skill_id, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_source ON public.skill_evidence(user_id, skill_id, source_id, kind) WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.proficiency_snapshots (
  snapshot_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id),
  proficiency_score NUMERIC(4,3) NOT NULL CHECK (proficiency_score BETWEEN 0 AND 5),
  confidence_score NUMERIC(4,3) NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
  evidence_count INT NOT NULL, proficiency_version TEXT NOT NULL,
  last_assessed_at TIMESTAMPTZ, snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_snap_user_skill ON public.proficiency_snapshots(user_id, skill_id, snapshot_at DESC);

-- ---------- assessments -------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.assessments (
  assessment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id), title TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS public.assessment_questions (
  question_id TEXT NOT NULL, version INT NOT NULL DEFAULT 1,
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id),
  type TEXT NOT NULL, difficulty SMALLINT NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  body JSONB NOT NULL,                       -- prompt/options/key/rubric; server-side only, never sent to learners
  provenance TEXT NOT NULL DEFAULT 'authored', objective TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (question_id, version));
CREATE INDEX IF NOT EXISTS idx_questions_skill ON public.assessment_questions(skill_id, difficulty);
CREATE TABLE IF NOT EXISTS public.assessment_attempts (
  attempt_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','completed','abandoned')),
  idempotency_key TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), completed_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS idx_attempts_user ON public.assessment_attempts(user_id, started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempt_idem ON public.assessment_attempts(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.assessment_answers (
  attempt_id UUID NOT NULL REFERENCES public.assessment_attempts(attempt_id) ON DELETE CASCADE,
  question_id TEXT NOT NULL, question_version INT NOT NULL,
  answer JSONB, earned NUMERIC(5,4) CHECK (earned BETWEEN 0 AND 1), needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  feedback TEXT, PRIMARY KEY (attempt_id, question_id),
  FOREIGN KEY (question_id, question_version) REFERENCES public.assessment_questions(question_id, version));

-- ---------- roadmaps ----------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.learning_paths (
  path_id UUID NOT NULL, version INT NOT NULL,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  goal_id UUID REFERENCES public.learning_goals(goal_id), hours_per_week NUMERIC(5,1) NOT NULL,
  change_summary JSONB NOT NULL DEFAULT '[]', generator TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (path_id, version));
CREATE INDEX IF NOT EXISTS idx_paths_user ON public.learning_paths(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS public.learning_path_items (
  item_id UUID NOT NULL, path_id UUID NOT NULL, path_version INT NOT NULL,
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id), position INT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'learn', status TEXT NOT NULL, from_level NUMERIC(3,2), target_level NUMERIC(3,2) NOT NULL,
  estimated_hours NUMERIC(6,1) NOT NULL, spec JSONB NOT NULL, completed_at TIMESTAMPTZ,
  PRIMARY KEY (item_id, path_version),
  FOREIGN KEY (path_id, path_version) REFERENCES public.learning_paths(path_id, version) ON DELETE CASCADE);

-- ---------- resources & content -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.resources (
  resource_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), title TEXT NOT NULL, url TEXT NOT NULL,
  canonical_key TEXT NOT NULL, skill_id UUID NOT NULL REFERENCES public.skills(skill_id),
  source_type TEXT NOT NULL, difficulty SMALLINT NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  description TEXT NOT NULL DEFAULT '',
  trust_status TEXT NOT NULL DEFAULT 'unverified' CHECK (trust_status IN ('unverified','verified','unreachable','blocked')),
  last_verified_at TIMESTAMPTZ, published_at TIMESTAMPTZ, metadata JSONB NOT NULL DEFAULT '{}',
  UNIQUE (skill_id, canonical_key));
CREATE TABLE IF NOT EXISTS public.content_versions (
  content_id UUID NOT NULL, version INT NOT NULL,
  user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,        -- NULL = shared content, else personalised
  skill_id UUID NOT NULL REFERENCES public.skills(skill_id), target_level NUMERIC(3,1) NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','partial','reviewed','published')),
  module JSONB NOT NULL, sources JSONB NOT NULL DEFAULT '[]', generated_by TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (content_id, version));
CREATE TABLE IF NOT EXISTS public.content_blocks (
  content_id UUID NOT NULL, version INT NOT NULL, position INT NOT NULL, section TEXT NOT NULL,
  block_type TEXT NOT NULL, payload JSONB NOT NULL, PRIMARY KEY (content_id, version, position),
  FOREIGN KEY (content_id, version) REFERENCES public.content_versions(content_id, version) ON DELETE CASCADE);

-- ---------- tutor, agents, audit ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tutor_conversations (
  conversation_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE, skill_id UUID REFERENCES public.skills(skill_id),
  summary TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_conv_user ON public.tutor_conversations(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS public.tutor_messages (
  message_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  conversation_id UUID NOT NULL REFERENCES public.tutor_conversations(conversation_id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')), content TEXT NOT NULL,
  tokens INT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_msgs_conv ON public.tutor_messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS public.agent_runs (
  run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE, workflow TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled','needs_human_review')),
  state JSONB NOT NULL DEFAULT '{}', prompt_tokens INT NOT NULL DEFAULT 0, completion_tokens INT NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS idx_runs_user ON public.agent_runs(user_id, started_at DESC);
CREATE TABLE IF NOT EXISTS public.workflow_events (
  event_id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES public.agent_runs(run_id) ON DELETE CASCADE,
  event TEXT NOT NULL, data JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_events_run ON public.workflow_events(run_id, event_id);
CREATE TABLE IF NOT EXISTS public.audit_logs (
  audit_id BIGSERIAL PRIMARY KEY, user_id UUID, action TEXT NOT NULL, entity TEXT, entity_id TEXT,
  ip INET, details JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_audit_user ON public.audit_logs(user_id, created_at DESC);

-- ---------- RLS: deny-by-default on every new table. Backend uses the service-role key (backend-only). ----
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['learner_profiles','skills','skill_dependencies','career_roles','career_skill_requirements',
    'learning_goals','skill_evidence','proficiency_snapshots','assessments','assessment_questions','assessment_attempts',
    'assessment_answers','learning_paths','learning_path_items','resources','content_versions','content_blocks',
    'tutor_conversations','tutor_messages','agent_runs','workflow_events','audit_logs']
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
  END LOOP;
END $$;

COMMIT;
