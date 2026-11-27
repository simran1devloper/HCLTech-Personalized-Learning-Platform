-- Rollback for 001. Drops ONLY objects created by 001 (new learning-intelligence tables). Existing users /
-- learning_history rows are untouched. NOTE: dropping loses data written to the new tables since the up-migration;
-- take a pg_dump of those tables first (see MIGRATION_GUIDE.md). The RLS hardening of users/learning_history is
-- intentionally NOT reverted (reverting would re-expose password hashes).
BEGIN;
DROP TABLE IF EXISTS public.workflow_events, public.agent_runs, public.audit_logs, public.tutor_messages,
  public.tutor_conversations, public.content_blocks, public.content_versions, public.resources,
  public.learning_path_items, public.learning_paths, public.assessment_answers, public.assessment_attempts,
  public.assessment_questions, public.assessments, public.proficiency_snapshots, public.skill_evidence,
  public.learning_goals, public.career_skill_requirements, public.career_roles, public.skill_dependencies,
  public.skills, public.learner_profiles CASCADE;
DROP FUNCTION IF EXISTS public.reject_dependency_cycle();
DROP FUNCTION IF EXISTS public.touch_updated_at();
COMMIT;
