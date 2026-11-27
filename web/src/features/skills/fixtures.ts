import type { CareerSkillGraph, GraphEdge, GraphNode, Recommendation, SkillDetail } from '@/types/api'

/** Test fixtures: a small AI-engineer-like graph.  python -> ml -> llm -> rag ; python -> sql -> pg -> rag ; llm -> prompt */
export const node = (id: string, o: Partial<GraphNode> = {}): GraphNode => ({
  skill_id: id, slug: id, name: id.toUpperCase(), category: 'ai', difficulty: 2, estimated_hours: 20, description: '', status: 'not_started', is_target: false, importance: 0, critical: false,
  target: 3, proficiency: 0, confidence: 0, evidence_count: 0, mastery: 0, learning_progress: 0, revision_reason: null, missing_prerequisites: [], prerequisites: [], unlocks: 0, depth: 0, stage: 0,
  hours_remaining: 12, ...o })
export const edge = (source: string, target: string, satisfied = true): GraphEdge => ({ source, target, min_proficiency: 2, satisfied })

export const NODES: GraphNode[] = [
  node('py', { name: 'Python', category: 'programming', status: 'mastered', mastery: 0.9, proficiency: 4.2, confidence: 0.8, evidence_count: 3, is_target: true, importance: 1, critical: true, target: 4, unlocks: 2, hours_remaining: 0 }),
  node('sql', { name: 'SQL', category: 'data', status: 'in_progress', mastery: 0.3, proficiency: 1.2, evidence_count: 1, confidence: 0.3, is_target: true, importance: 0.8, target: 3, depth: 0, unlocks: 1, hours_remaining: 9 }),
  node('ml', { name: 'Machine Learning', category: 'ml', status: 'ready', depth: 1, prerequisites: ['py'], unlocks: 1, hours_remaining: 40 }),
  node('pg', { name: 'PostgreSQL', category: 'data', status: 'not_started', depth: 1, prerequisites: ['sql'], missing_prerequisites: ['sql'], unlocks: 1 }),
  node('llm', { name: 'LLM Fundamentals', category: 'ai', status: 'blocked', depth: 2, prerequisites: ['ml'], missing_prerequisites: ['ml'], is_target: true, importance: 1, critical: true, target: 3.5, unlocks: 2, evidence_count: 1, proficiency: 1, mastery: 0.2, hours_remaining: 16 }),
  node('prompt', { slug: 'prompt-engineering', name: 'Prompt Engineering', category: 'ai', status: 'not_started', depth: 3, prerequisites: ['llm'], missing_prerequisites: ['ml', 'llm'], is_target: true, importance: 0.6 }),
  node('rag', { name: 'RAG', category: 'ai', status: 'needs_revision', revision_reason: 'stale', depth: 3, prerequisites: ['llm', 'pg'], missing_prerequisites: ['ml', 'llm', 'sql', 'pg'], is_target: true, importance: 0.9, critical: true, target: 3.5, evidence_count: 2, proficiency: 3.6, mastery: 0.8, hours_remaining: 6 }),
]
export const EDGES: GraphEdge[] = [edge('py', 'ml'), edge('py', 'sql', true), edge('sql', 'pg', false), edge('ml', 'llm', false), edge('llm', 'prompt', false), edge('llm', 'rag', false), edge('pg', 'rag', false)]

export const recs: Recommendation[] = [
  { skill_id: 'ml', name: 'Machine Learning', rank: 1, priority: 'high', score: 0.52, explanation: 'Machine Learning: 0.0/3.0; prerequisite for your career skills.', missing_prerequisites: [], unlocks: ['llm'], suggested_action: 'start_learning', next_skill_id: null, drivers: { relevance: 0.2 } },
  { skill_id: 'llm', name: 'LLM Fundamentals', rank: 2, priority: 'medium', score: 0.3, explanation: 'LLM Fundamentals: 1.0/3.5; critical career requirement.', missing_prerequisites: ['ml'], unlocks: [], suggested_action: 'learn_prerequisites', next_skill_id: 'ml', drivers: { relevance: 0.2 } },
]
export const graph = (o: Partial<CareerSkillGraph> = {}): CareerSkillGraph => ({
  role: { slug: 'ai-engineer', title: 'AI Engineer', description: 'Builds LLM-powered products.' }, nodes: NODES, edges: EDGES,
  metrics: { skills_total: 7, skills_covered: 4, mastered: 1, in_progress: 2, needs_revision: 1, remaining: 6, coverage: 0.571, mastery: 0.33, readiness: 0.42, readiness_confidence: 0.55, critical_gaps: ['llm', 'rag'], version: 'mastery-v1' },
  categories: ['ai', 'data', 'ml', 'programming'], stages: ['Foundations', 'Core Skills', 'Applied Skills', 'Advanced Skills', 'Career Specialization'], recommendations: recs, generated_at: '2026-06-01T00:00:00Z', ...o })
export const detail = (id = 'llm', o: Partial<SkillDetail> = {}): SkillDetail => ({
  skill: NODES.find((n) => n.skill_id === id)!, career: { slug: 'ai-engineer', title: 'AI Engineer' },
  prerequisites: [{ skill_id: 'ml', name: 'Machine Learning', status: 'ready', proficiency: 0.4, mastery: 0.1, category: 'ml', required_level: 1, met: false }],
  missing_prerequisites: [{ skill_id: 'ml', name: 'Machine Learning', status: 'ready', proficiency: 0.4, mastery: 0.1, category: 'ml' }],
  unlocks: [{ skill_id: 'rag', name: 'RAG', status: 'needs_revision', proficiency: 3.6, mastery: 0.8, category: 'ai' }, { skill_id: 'prompt', name: 'Prompt Engineering', status: 'not_started', proficiency: 0, mastery: 0, category: 'ai' }],
  related: [], aliases: [], evidence: [{ kind: 'quiz', score: 0.5, difficulty: 3, n_items: 8, occurred_at: '2026-05-20T00:00:00Z', source_id: 'a1', graded: true }],
  learning_history: [{ session_id: 's1', title: 'LLM basics', status: 'active', completed_sections: 2, total_sections: 5, updated_at: '2026-05-21T00:00:00Z' }], ...o })
