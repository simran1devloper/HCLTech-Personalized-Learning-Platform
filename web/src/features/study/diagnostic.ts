import type { DiagnosticPlan, GenerationSource } from '@/types/api'

export interface StartStep { id: string; label: string }

/** The progress steps for a start, derived from what the backend said it will actually do. AI wording appears ONLY when the plan involves the model. */
export function stepsFor(plan: DiagnosticPlan | null): StartStep[] {
  if (!plan) return [{ id: 'check', label: 'Checking question bank…' }]
  if (!plan.will_call_ai) return [{ id: 'check', label: 'Checking question bank…' }, { id: 'found', label: 'Found this topic in the question bank.' }, { id: 'prepare', label: 'Preparing your diagnostic quiz…' }]
  if (!plan.known || plan.eligible === 0) return [{ id: 'check', label: 'Checking question bank…' }, { id: 'missing', label: 'Topic not found in question bank.' }, { id: 'generate', label: 'Generating and validating questions…' }, { id: 'prepare', label: 'Preparing your quiz…' }]
  return [{ id: 'check', label: 'Checking question bank…' }, { id: 'partial', label: `Found ${plan.eligible} saved question${plan.eligible === 1 ? '' : 's'}; ${plan.missing} more needed.` },
    { id: 'generate', label: `Generating and validating ${plan.missing} more…` }, { id: 'prepare', label: 'Preparing your quiz…' }]
}

export const SOURCE_LABEL: Record<GenerationSource, string> = { question_bank: 'Question bank', ai_generated: 'Newly generated', hybrid: 'Bank + generated' }
export const normalizeTopic = (t: string) => t.replace(/\s+/g, ' ').trim()
export const topicError = (t: string): string | null => {
  const n = normalizeTopic(t)
  if (n.length < 2) return 'Enter a topic of at least two characters.'
  if (n.length > 80) return 'Topics can be at most 80 characters.'
  return /^[\p{L}\p{N} .+#&/'()-]+$/u.test(n) ? null : 'Use letters, numbers and spaces only.'
}
