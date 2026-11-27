/** Types mirror backend/app/api/v1 responses (see API_CONTRACTS.md). */
export interface ApiErrorBody { code: string; message: string; details?: Record<string, unknown> }
export interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export interface User { id: string; name: string; email: string }
export interface TokenResponse { access_token: string; token_type: string; user: User }

export interface Skill {
  skill_id: string; slug: string; name: string; description: string; category: string; domain: string
  difficulty: number; aliases: string[]; parent_skill_id: string | null; status: string; prerequisites: string[]
}
export interface Contribution { source_id: string | null; kind: string; implied_level: number; weight: number; share: number }
export interface Proficiency {
  skill_id: string; proficiency_score: number; confidence_score: number; evidence_count: number
  last_assessed_at: string | null; proficiency_version: string; explanation: Contribution[]
}
export interface ProficiencyHistory {
  skill_id: string; skill: string; current: Proficiency | null
  snapshots: { proficiency_score: number; confidence_score: number; evidence_count: number; snapshot_at: string; proficiency_version: string }[]
  evidence: { kind: string; score: number; difficulty: number; n_items: number; occurred_at: string; source_id: string | null }[]
}
export interface CareerRole { slug: string; title: string; requirements: { skill_slug: string; target_level: number; importance: number; critical: boolean }[] }
export interface Goal { goal_id: string; role_slug: string; hours_per_week: number; created_at: string }
export interface GapItem {
  skill_id: string; name: string; current: number; target: number; gap: number; confidence: number
  missing_prerequisites: string[]; priority: number; blocked: boolean; explanation: string
}
export interface GapReport { items: GapItem[]; recommended_next: string | null }

// ---- Skill Explorer (backend/app/api/v1/skill_explorer_routes.py) ----
export type SkillStatus = 'mastered' | 'in_progress' | 'ready' | 'blocked' | 'not_started' | 'needs_revision'
export interface GraphNode {
  skill_id: string; slug: string; name: string; category: string; difficulty: number; estimated_hours: number; description: string
  status: SkillStatus; is_target: boolean; importance: number; critical: boolean; target: number; proficiency: number; confidence: number
  evidence_count: number; mastery: number; learning_progress: number; revision_reason: 'stale' | 'regression' | null
  missing_prerequisites: string[]; prerequisites: string[]; unlocks: number; depth: number; stage: number; hours_remaining: number
}
export interface GraphEdge { source: string; target: string; min_proficiency: number; satisfied: boolean }
export interface CareerMetrics {
  skills_total: number; skills_covered: number; mastered: number; in_progress: number; needs_revision: number; remaining: number
  coverage: number; mastery: number; readiness: number; readiness_confidence: number; critical_gaps: string[]; version: string
}
export type RecAction = 'revise' | 'learn_prerequisites' | 'start_learning' | 'continue_learning' | 'take_assessment'
export interface Recommendation {
  skill_id: string; name: string; rank: number; priority: 'high' | 'medium' | 'low'; score: number; explanation: string
  missing_prerequisites: string[]; unlocks: string[]; suggested_action: RecAction; next_skill_id: string | null; drivers: Record<string, number>
}
export interface CareerSkillGraph {
  role: { slug: string; title: string; description: string } | null
  nodes: GraphNode[]; edges: GraphEdge[]; metrics: CareerMetrics; categories: string[]; stages: string[]
  recommendations: Recommendation[]; generated_at: string
}
export interface SkillBrief { skill_id: string; name: string; status: SkillStatus; proficiency: number; mastery: number; category: string }
export interface SkillDetail {
  skill: GraphNode; career: { slug: string; title: string } | null
  prerequisites: (SkillBrief & { required_level: number; met: boolean })[]
  missing_prerequisites: SkillBrief[]; unlocks: SkillBrief[]; related: SkillBrief[]; aliases: string[]
  evidence: { kind: string; score: number; difficulty: number; n_items: number; occurred_at: string; source_id: string | null; graded: boolean }[]
  learning_history: { session_id: string; title: string; status: string; completed_sections: number; total_sections: number; updated_at: string }[]
}
export interface Resource { resource_id?: string; title?: string; url?: string; source?: string; [k: string]: unknown }

export type QuestionType = 'multiple_choice' | 'multiple_select' | 'true_false' | 'short_answer' | 'coding' | 'debugging' | 'scenario' | 'system_design' | 'practical'
export interface AssessmentQuestion { question_id: string; type: QuestionType; prompt: string; options: string[]; difficulty: number; objective: string; kind?: string; code_snippet?: string | null; estimated_time_seconds?: number }
export type GenerationStatus = 'generating' | 'completed' | 'failed' | 'cancelled'
export interface GenerationQuestion { question_id: string; index: number; concept: string; difficulty: number; question_type: string; prompt: string; source: string }
/** GET /assessments/{id}/generation: the persisted state used to recover after a refresh or a dropped stream. */
export interface GenerationState { assessment_id: string; status: GenerationStatus; requested: number; completed: number; progress: number; last_seq: number; error: string | null; questions: GenerationQuestion[] }
/** One server-sent lifecycle event (see the SSE contract in docs/implementation/assessment-generation-sse.md). */
export interface GenerationEvent {
  assessment_id: string; type: string; seq: number; status: GenerationStatus; completed: number; requested: number; progress: number; timestamp: string
  stage?: string; question?: Partial<GenerationQuestion>; valid?: boolean; reason?: string; message?: string; streamed?: number; generated?: number
}
export interface Assessment {
  assessment_id: string; skill_id: string; skill?: string; questions: AssessmentQuestion[]; requested?: number; total_questions?: number; shortfall?: number
  status?: 'open' | GenerationStatus; generation?: GenerationState; reattached?: boolean
  estimated_minutes?: number; note?: string | null; source?: { bank_questions: number; generated_inline: number; llm_calls: number }
}
export interface AssessmentDetail {
  assessment_id: string; skill_id: string; skill: string; status: 'open' | 'completed'; started_at: string; questions: AssessmentQuestion[]
  total_questions?: number; answered_questions?: number; answers?: Record<string, unknown>; learning_session_id?: string | null
  requested?: number; generation_status?: GenerationStatus | null
}
export interface GradeResult { question_id: string; earned: number; correct: boolean; feedback: string; needs_review: boolean }
export type ConceptLevel = 'strong' | 'good' | 'developing' | 'needs_improvement' | 'unknown'
export interface ConceptResult { concept: string; questions_attempted: number; correct_answers: number; accuracy: number; proficiency_level: ConceptLevel; identified_gap: boolean }
export interface SkillGapReport {
  skill: string; skill_id: string; overall_score: number | null; overall_level: ConceptLevel; questions: number
  strong_concepts: string[]; weak_concepts: string[]; critical_gaps: string[]; unknown_concepts: string[]
  recommended_sections: { concept: string; key: string; priority: string; accuracy: number }[]; recommended_learning_order: string[]
  prerequisite_gaps: { skill_id: string; name: string; required: number; current: number | null }[]
  by_difficulty: { difficulty: number; questions: number; accuracy: number }[]; concepts: ConceptResult[]; caveat: string; basis: string
}
export interface ReviewItem {
  question_id: string; prompt: string; code_snippet?: string | null; options: string[]; selected: unknown; correct_options: number[]; is_correct: boolean | null
  explanation: string; solution_approach?: string | null; concept: string; difficulty: number
}
export interface AssessmentResults {
  assessment_id: string; status: string; skill_id: string; score: number | null; results: GradeResult[]; proficiency: Proficiency | null
  skill?: string; score_pct?: number | null; correct_count?: number; incorrect_count?: number; total_questions?: number; time_taken_seconds?: number
  proficiency_level?: ConceptLevel; difficulty_level?: string | null; concepts?: ConceptResult[]; skill_gaps?: SkillGapReport; review?: ReviewItem[]
  learning_session_id?: string | null; already_submitted?: boolean
}
export interface BankSkill { skill_id: string; name: string; category: string; question_count: number; concepts: number; proficiency: number | null; assessed: boolean; est_seconds_per_question: number; recommended: boolean }
export interface GenerationProgress { skill_id: string; state: 'idle' | 'running' | 'done' | 'stopped' | 'failed'; target: number; added: number; percent: number | null; stopped?: boolean }
export interface AssessmentSummary { assessment_id: string; skill_id: string; skill: string; status: 'open' | 'completed'; started_at: string; completed_at: string | null; n_questions: number; score: number | null }

export type MilestoneStatus = 'pending' | 'in_progress' | 'completed' | 'skipped_mastered' | 'blocked' | 'superseded'
export interface Milestone {
  item_id: string; skill_id: string; skill_name: string; order: number; kind: string; from_level: number; target_level: number
  prerequisites: string[]; estimated_hours: number; objectives: string[]; resource_ids: string[]; checkpoint: string
  completion_criteria: { min_proficiency: number; min_confidence: number }; status: MilestoneStatus; blocked_by: string[]; completed_at: string | null
}
export interface Roadmap {
  roadmap_id: string; learner_id: string; goal: string; version: number; hours_per_week: number; milestones: Milestone[]
  total_hours: number; estimated_weeks: number; created_at: string; change_summary: string[]
}
export interface RoadmapSummary { roadmap_id: string; goal: string; version: number; hours_per_week: number; estimated_weeks: number; created_at: string; milestones_total: number; milestones_done: number }
export interface ReplanResult { roadmap: Roadmap; changed: boolean; diff: { added: string[]; removed: string[]; skipped: string[]; reintroduced: string[]; unblocked: string[]; blocked: string[]; preserved_completed: number } }

export interface Readiness { role_slug: string; readiness: number; evidence_confidence: number; critical_gaps: string[]; unassessed: string[]; basis: string }

export interface AnalyticsSummary {
  skills_assessed: number; assessments_completed: number
  weekly_activity: { week_start: string; assessments: number; questions: number }[]
  accuracy_trend: { at: string; score: number; skill_id: string; skill: string }[]
  proficiency_series: { skill_id: string; skill: string; points: { at: string; score: number; confidence: number }[] }[]
  consistency: { active_days_last_28: number; ratio: number; current_streak_days: number; basis: string }
  roadmap_completion: { done: number; total: number; ratio: number | null; roadmaps: { roadmap_id: string; goal: string; version: number; done: number; total: number }[] }
  strongest: { skill_id: string; skill: string; proficiency: number; confidence: number }[]
  weakest: { skill_id: string; skill: string; proficiency: number; confidence: number }[]
  retention: { skill_id: string; skill: string; first: number; last: number; delta: number; days_between: number }[]
  learning_hours: number | null
  unavailable: Record<string, string>
  career: null | {
    readiness_trend: { date: string; readiness: number; evidence_confidence: number }[]
    role_slug: string; title: string; readiness: number; evidence_confidence: number; critical_gaps: string[]; unassessed: string[]; basis: string
    gap_summary: { open_gaps: number; blocked: number; top: { skill_id: string; name: string; current: number; target: number; gap: number; priority: number; blocked: boolean }[] }
    recommended_next: { skill_id: string; name: string } | null
  }
  insights: { kind: string; text: string }[]
  insights_basis: string
}
export interface TutorConversation { conversation_id: string; title: string; skill_id: string | null; language: 'en' | 'hinglish'; created_at: string; updated_at: string; pinned?: boolean; preview?: string }
export type TutorMode = 'auto' | 'learn' | 'code' | 'practice' | 'interview' | 'system_design' | 'revise'
export type TutorLevel = 'auto' | 'beginner' | 'intermediate' | 'advanced'
export interface TutorChatOptions { skill?: string; language: 'en' | 'hinglish'; mode?: TutorMode; level?: TutorLevel; career_goal?: string }
export interface TutorSource { kind: string; label: string; link: string }
export interface TutorCard { kind: string; title: string; data: Record<string, any>; link: string | null; link_label: string | null; empty: string | null; message?: string }
export interface TutorTool { execution_id: string; message_id: number; tool: string; status: 'pending' | 'running' | 'succeeded' | 'failed' | 'dismissed'; args: Record<string, any>; result: TutorCard | null }
export interface TutorMeta { intent?: string | null; provider?: string | null; model?: string | null; error_code?: string | null; followups?: string[]; sources?: TutorSource[]; truncated?: boolean; context_unavailable?: boolean }
export interface TutorMessage {
  role: 'user' | 'assistant'; content: string; created_at: string; message_id?: number; status?: 'complete' | 'streaming' | 'cancelled' | 'interrupted' | 'failed'
  client_message_id?: string | null; meta?: TutorMeta | null; feedback?: 'up' | 'down' | null; tools?: TutorTool[]
}

// Legacy /learning API (kept for the Diagnostic Study flow)
export interface LegacyQuestion { question: string; options: string[]; correct_answer: string }
export interface LegacyQuiz { topic: string; questions: LegacyQuestion[]; quiz_token?: string }
export interface LegacyQuizResult { topic: string; score: number; total: number; level: 'Beginner' | 'Intermediate' | 'Expert'; breakdown: { question: string; user_answer: string | null; correct_answer: string; correct: boolean }[] }
export interface LegacyContent {
  topic: string; level: string; summary: string; key_concepts: unknown; deep_dive: string; real_world_applications: string
  viva_voice: string; best_practices?: string; practical_guide?: string; learning_roadmap: string; flashcards: { question: string; answer: string }[]
  recommendations: { weak_areas: string[]; next_topics: unknown[]; practice_suggestions: string[]; resources: unknown[] }; agent_intro?: string
}
export interface LegacyHistoryItem { id: string; topic: string; score: number; level: string; created_at: string }

// ---- Content history (persistent, user-owned study sessions) ----
export type SessionStatus = 'active' | 'completed' | 'generating' | 'failed' | 'archived'
export type SectionStatus = 'NOT_STARTED' | 'QUEUED' | 'GENERATING' | 'COMPLETED' | 'FAILED'
export type HistoryFilter = 'all' | 'completed' | 'in_progress' | 'failed' | 'archived'
export type HistorySort = 'updated' | 'created' | 'accessed' | 'alphabetical'
export interface ContentSessionSummary {
  id: string; title: string; description: string; topic: string; content_type: string; learning_level: string; language: string; status: SessionStatus
  total_sections: number; completed_sections: number; created_at: string; updated_at: string; last_accessed_at: string | null; archived_at: string | null; tags: string[]
}
export interface SessionSection { section_key: string; section_title: string; status: SectionStatus; content_version: number; has_content: boolean; updated_at: string }
export interface SessionJob { id: string; section_key: string; status: string }
export interface ContentSessionDetail extends ContentSessionSummary { sections: SessionSection[]; active_jobs: SessionJob[]; metadata: Record<string, unknown>; context?: Record<string, unknown> }
export interface SessionContent { session_id: string; topic: string; level: string; sections: Record<string, unknown>; incomplete_sections: string[] }
export interface SectionVersion { version: number; active: boolean; created_at: string; model_name: string | null; input_tokens: number; output_tokens: number }

// ---- Diagnostic Study (database-first quiz) ----
export type GenerationSource = 'question_bank' | 'ai_generated' | 'hybrid'
export interface DiagnosticPlan { topic: string; canonical_topic: string | null; known: boolean; plan: GenerationSource; eligible: number; requested: number; missing: number; will_call_ai: boolean }
export interface DiagnosticBreakdown { question: string; user_answer: string | null; correct_answer: string | null; correct: boolean; explanation?: string }
export interface DiagnosticResult { topic: string; score: number; total: number; score_pct: number; score_10: number; level: 'Beginner' | 'Intermediate' | 'Expert'; breakdown: DiagnosticBreakdown[] }
export interface DiagnosticSession {
  session_id: string; topic: string; question_count: number; questions: AssessmentQuestion[]; generation_source: GenerationSource | null; status: string; answers: Record<string, unknown>
  requested_count?: number; shortfall?: number; note?: string | null; resumed?: boolean; answered_questions?: number; result?: DiagnosticResult | null
}
export interface DiagnosticSessionSummary { session_id: string; topic: string; status: 'open' | 'completed'; question_count: number; answered_questions: number; score_pct: number | null; level: string | null; generation_source: GenerationSource | null; started_at: string; completed_at: string | null }
export interface TopicSuggestion { skill_id: string; name: string; category: string; question_count: number }

// ---- Study workspace ----
export interface PipelineSection { section_key: string; title: string; status: string; version: number; source: 'generated' | 'reused' | null; input_tokens: number; output_tokens: number; latency_ms: number; attempts: number; job_status: string | null; error: string | null; error_code: string | null }
export interface PipelineReport { sections: PipelineSection[]; active_jobs: number; totals: { sections: number; completed: number; generated: number; reused: number; failed: number; input_tokens: number; output_tokens: number; latency_ms: number }; profile: StudyProfile | null }
export interface StudyProfile { career: { slug: string; title: string } | null; level: string; weak: string[]; strong: string[]; diagnostic_session_id: string | null; fingerprint: string }
export interface SectionState { understood: boolean; bookmarked: boolean; note: string; opened_at: string | null; updated_at?: string }
export interface WorkspaceState { sections: Record<string, SectionState>; progress: { total: number; understood: number; opened: number; bookmarked: number; understood_pct: number; reading_pct: number } }
export interface KnowledgeCheck { available: boolean; check_id: string | null; status?: string; questions: AssessmentQuestion[]; message?: string }
export interface KnowledgeCheckResult { check_id: string; score: number; total: number; passed: boolean; results: { question_id: string; correct: boolean; selected: unknown; correct_options: number[]; explanation: string }[] }
