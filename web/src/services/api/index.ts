import { http } from '@/services/http'
import type * as T from '@/types/api'

const q = (o: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
type Sig = { signal?: AbortSignal }

export const authApi = {
  login: (b: { email: string; password: string }) => http<T.TokenResponse>('/auth/login', { method: 'POST', body: b, auth: false }),
  register: (b: { name: string; email: string; password: string }) => http<T.TokenResponse>('/auth/register', { method: 'POST', body: b, auth: false }),
}
export const skillsApi = {
  list: (p: { q?: string; category?: string; offset?: number; limit?: number } = {}, s: Sig = {}) =>
    http<T.Page<T.Skill>>('/api/v1/skills' + q({ limit: 200, ...p }), s),
  history: (id: string, s: Sig = {}) => http<T.ProficiencyHistory>(`/api/v1/proficiency/${id}/history`, s),
}
export const proficiencyApi = {
  list: (s: Sig = {}) => http<{ items: T.Proficiency[] }>('/api/v1/proficiency', s),
  gaps: (role: string, s: Sig = {}) => http<T.GapReport>('/api/v1/skill-gaps' + q({ role }), s),
}
export const ALL_SKILLS = 'all'
export const explorerApi = {
  /** `ALL_SKILLS` is the career-agnostic view of the whole registry (no readiness or recommendations). */
  graph: (role: string, s: Sig = {}) => http<T.CareerSkillGraph>(role === ALL_SKILLS ? '/api/v1/skill-graph' : `/api/v1/careers/${encodeURIComponent(role)}/skill-graph`, s),
  detail: (skill: string, role?: string, s: Sig = {}) => http<T.SkillDetail>(`/api/v1/skills/${encodeURIComponent(skill)}/detail` + q({ role: role === ALL_SKILLS ? undefined : role }), s),
  recommendations: (role: string, limit = 5, s: Sig = {}) => http<T.Recommendation[]>(`/api/v1/careers/${encodeURIComponent(role)}/recommendations` + q({ limit }), s),
  search: (p: { q?: string; category?: string; status?: string; role?: string; offset?: number; limit?: number }, s: Sig = {}) => http<T.Page<T.GraphNode>>('/api/v1/skills/search' + q(p), s),
  prerequisites: (skill: string, role?: string, s: Sig = {}) => http<T.GraphNode[]>(`/api/v1/skills/${encodeURIComponent(skill)}/prerequisites` + q({ role }), s),
  dependents: (skill: string, role?: string, s: Sig = {}) => http<T.GraphNode[]>(`/api/v1/skills/${encodeURIComponent(skill)}/dependents` + q({ role }), s),
  addEvidence: (skill: string, b: { kind: 'exercise' | 'coding' | 'project'; score: number; difficulty: number; n_items?: number; source_id: string }, role?: string) =>
    http<T.SkillDetail>(`/api/v1/skills/${encodeURIComponent(skill)}/evidence` + q({ role }), { method: 'POST', body: b }),
  resources: (skill: string, limit = 5) => http<{ items: { resource: T.Resource; score: number }[]; warnings: string[]; sources_configured: number }>('/api/v1/resources/discover', { method: 'POST', body: { skill, limit } }),
}
export const careerApi = {
  roles: (s: Sig = {}) => http<T.CareerRole[]>('/api/v1/careers', s),
  readiness: (slug: string, s: Sig = {}) => http<T.Readiness>(`/api/v1/careers/${slug}/readiness`, s),
  goals: (s: Sig = {}) => http<T.Goal[]>('/api/v1/goals', s),
  createGoal: (b: { role_slug: string; hours_per_week: number }) => http<T.Goal>('/api/v1/goals', { method: 'POST', body: b }),
}
export const assessmentApi = {
  list: (p: { offset?: number; limit?: number } = {}, s: Sig = {}) => http<T.Page<T.AssessmentSummary>>('/api/v1/assessments' + q(p), s),
  get: (id: string, s: Sig = {}) => http<T.AssessmentDetail>(`/api/v1/assessments/${id}`, s),
  create: (b: { skill: string; n_questions: number }, key?: string) =>
    http<T.Assessment>('/api/v1/assessments', { method: 'POST', body: b, headers: key ? { 'X-Idempotency-Key': key } : undefined }),
  submit: (id: string, answers: Record<string, unknown>) => http<T.AssessmentResults>(`/api/v1/assessments/${id}/submit`, { method: 'POST', body: { answers } }),
  results: (id: string, s: Sig = {}) => http<T.AssessmentResults>(`/api/v1/assessments/${id}/results`, s),
  /** Autosave: answers are stored server-side as they are chosen, so a refresh or another device restores them. */
  saveAnswers: (id: string, answers: Record<string, unknown>, times?: Record<string, number>, clear?: string[]) =>
    http<{ saved: number; answered_questions: number; total_questions: number }>(`/api/v1/assessments/${id}/answers`, { method: 'PUT', body: { answers, times, clear } }),
  /** Persisted generation state (recovery) and the learner's generating assessment, if any (page refresh). */
  generation: (id: string, s: Sig = {}) => http<T.GenerationState>(`/api/v1/assessments/${id}/generation`, s),
  activeGeneration: (s: Sig = {}) => http<{ active: T.Assessment | null }>('/api/v1/assessments/generations/active', s),
  cancelGeneration: (id: string) => http<T.GenerationState>(`/api/v1/assessments/${id}/generation/cancel`, { method: 'POST' }),
  skillGaps: (id: string, s: Sig = {}) => http<T.SkillGapReport>(`/api/v1/assessments/${id}/skill-gaps`, s),
  generatePlan: (id: string) => http<{ session_id: string; created: boolean }>(`/api/v1/assessments/${id}/generate-learning-plan`, { method: 'POST' }),
}
export const questionBankApi = {
  skills: (qs?: string, s: Sig = {}) => http<{ items: T.BankSkill[]; total: number }>('/api/v1/assessment-question-bank/skills' + q({ q: qs }), s),
  progress: (skillId: string, s: Sig = {}) => http<T.GenerationProgress>(`/api/v1/assessment-question-bank/skills/${skillId}/generation-progress`, s),
  stop: (skillId: string) => http<T.GenerationProgress>(`/api/v1/assessment-question-bank/skills/${skillId}/generation/stop`, { method: 'POST' }),
}
export const roadmapApi = {
  list: (s: Sig = {}) => http<{ items: T.RoadmapSummary[] }>('/api/v1/roadmaps', s),
  get: (id: string, s: Sig = {}) => http<T.Roadmap>(`/api/v1/roadmaps/${id}`, s),
  versions: (id: string, s: Sig = {}) => http<{ items: T.Roadmap[] }>(`/api/v1/roadmaps/${id}/versions`, s),
  generate: (b: { role_slug: string; hours_per_week: number }, key?: string) =>
    http<T.Roadmap>('/api/v1/roadmaps/generate', { method: 'POST', body: b, headers: key ? { 'X-Idempotency-Key': key } : undefined }),
  replan: (id: string) => http<T.ReplanResult>(`/api/v1/roadmaps/${id}/replan`, { method: 'POST' }),
  complete: (id: string, item: string) => http<T.Milestone>(`/api/v1/roadmaps/${id}/milestones/${item}/complete`, { method: 'POST' }),
}
export const analyticsApi = {
  summary: (s: Sig = {}) => http<T.AnalyticsSummary>('/api/v1/analytics/summary', s),
  narrate: () => http<{ text: string; provider: string; model: string; basis: string }>('/api/v1/analytics/insights/narrate', { method: 'POST' }),
}
const TU = '/api/v1/tutor'
export const tutorApi = {
  conversations: (p: { q?: string } = {}, s: Sig = {}) => http<{ items: T.TutorConversation[] }>(`${TU}/conversations` + q(p), s),
  conversation: (id: string, s: Sig = {}) => http<T.TutorConversation & { messages: T.TutorMessage[] }>(`${TU}/conversations/${id}`, s),
  update: (id: string, b: { title?: string; pinned?: boolean }) => http<T.TutorConversation>(`${TU}/conversations/${id}`, { method: 'PATCH', body: b }),
  remove: (id: string) => http<null>(`${TU}/conversations/${id}`, { method: 'DELETE' }),
  stop: (messageId: number) => http<{ stopping: boolean }>(`${TU}/messages/${messageId}/stop`, { method: 'POST' }),
  feedback: (messageId: number, rating: 'up' | 'down' | null) => http<{ rating: string | null }>(`${TU}/messages/${messageId}/feedback`, { method: 'PUT', body: { rating } }),
  confirm: (executionId: string) => http<T.TutorTool>(`${TU}/actions/${executionId}/confirm`, { method: 'POST' }),
  dismiss: (executionId: string) => http<T.TutorTool>(`${TU}/actions/${executionId}/dismiss`, { method: 'POST' }),
}
export const legacyApi = {
  generateQuiz: (topic: string) => http<T.LegacyQuiz>('/learning/generate-quiz', { method: 'POST', body: { topic } }),
  submitQuiz: (b: { topic: string; answers: Record<string, number>; quiz_token?: string }) =>
    http<T.LegacyQuizResult>('/learning/submit-quiz', { method: 'POST', body: { ...b, questions: [] } }),
  generateContent: (b: { topic: string; level: string; score: number }) => http<T.LegacyContent>('/learning/generate-content', { method: 'POST', body: b }),
  history: (s: Sig = {}) => http<{ history: T.LegacyHistoryItem[] }>('/learning/history', s),
}

export interface HistoryListParams { q?: string; status?: T.HistoryFilter; content_type?: string; sort?: T.HistorySort; offset?: number; limit?: number }
const H = '/api/v1/content-sessions'
export const historyApi = {
  list: (p: HistoryListParams = {}, s: Sig = {}) => http<T.Page<T.ContentSessionSummary>>(H + q({ ...p }), s),
  get: (id: string, s: Sig = {}) => http<T.ContentSessionDetail>(`${H}/${id}`, s),
  content: (id: string, s: Sig = {}) => http<T.SessionContent>(`${H}/${id}/content`, s),
  rename: (id: string, title: string) => http<T.ContentSessionSummary>(`${H}/${id}`, { method: 'PATCH', body: { title } }),
  setTags: (id: string, tags: string[]) => http<T.ContentSessionSummary>(`${H}/${id}`, { method: 'PATCH', body: { tags } }),
  archive: (id: string) => http<T.ContentSessionSummary>(`${H}/${id}/archive`, { method: 'POST' }),
  restore: (id: string) => http<T.ContentSessionSummary>(`${H}/${id}/restore`, { method: 'POST' }),
  duplicate: (id: string) => http<T.ContentSessionSummary>(`${H}/${id}/duplicate`, { method: 'POST' }),
  remove: (id: string) => http<null>(`${H}/${id}`, { method: 'DELETE' }),
  versions: (id: string, key: string, s: Sig = {}) => http<{ items: T.SectionVersion[] }>(`${H}/${id}/sections/${key}/versions`, s),
  activateVersion: (id: string, key: string, version: number) => http<T.SessionSection>(`${H}/${id}/sections/${key}/versions/${version}/activate`, { method: 'POST' }),
  /** SSE endpoints (same event protocol as /learning/generate-content/stream) */
  path: { generate: (id: string, key: string) => `${H}/${id}/sections/${key}/generate`, regenerate: (id: string, key: string) => `${H}/${id}/sections/${key}/regenerate`, cont: (id: string) => `${H}/${id}/continue` },
}

const D = '/api/v1/diagnostic-study'
export const diagnosticApi = {
  suggest: (qs: string, s: Sig = {}) => http<{ items: T.TopicSuggestion[] }>(`${D}/suggest` + q({ q: qs }), s),
  /** Read-only preview of what a start will do (database vs AI); creates nothing. */
  check: (b: { topic: string; question_count: number }, s: Sig = {}) => http<T.DiagnosticPlan>(`${D}/check`, { method: 'POST', body: b, ...s }),
  start: (b: { topic: string; question_count: number }, key?: string, s: Sig = {}) => http<T.DiagnosticSession>(`${D}/start`, { method: 'POST', body: b, headers: key ? { 'X-Idempotency-Key': key } : undefined, ...s }),
  sessions: (s: Sig = {}) => http<{ items: T.DiagnosticSessionSummary[]; total: number }>(`${D}/sessions`, s),
  session: (id: string, s: Sig = {}) => http<T.DiagnosticSession>(`${D}/sessions/${id}`, s),
  submit: (id: string, answers: Record<string, unknown>) => http<T.DiagnosticSession>(`${D}/sessions/${id}/submit`, { method: 'POST', body: { answers } }),
}

const CS = '/api/v1/content-sessions'
export const workspaceApi = {
  pipeline: (id: string, s: Sig = {}) => http<T.PipelineReport>(`${CS}/${id}/pipeline`, s),
  state: (id: string, s: Sig = {}) => http<T.WorkspaceState>(`${CS}/${id}/state`, s),
  setState: (id: string, key: string, b: { understood?: boolean; bookmarked?: boolean; note?: string; opened?: boolean }) => http<T.SectionState>(`${CS}/${id}/sections/${key}/state`, { method: 'PUT', body: b }),
  startCheck: (id: string, key: string, b: { round?: number; n?: number } = {}) => http<T.KnowledgeCheck>(`${CS}/${id}/sections/${key}/knowledge-check`, { method: 'POST', body: b }),
  submitCheck: (checkId: string, answers: Record<string, unknown>) => http<T.KnowledgeCheckResult>(`/api/v1/knowledge-checks/${checkId}/submit`, { method: 'POST', body: { answers } }),
}
