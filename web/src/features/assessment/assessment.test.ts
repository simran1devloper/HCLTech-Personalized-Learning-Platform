import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AssessmentResultsView from './AssessmentResultsView.vue'
import { conceptAccuracyOption, describeConcepts, formatDuration, LEVEL_LABEL } from './resultCharts'
import { uuid } from '@/utils/id'
import type { AssessmentResults, ConceptResult, SkillGapReport } from '@/types/api'

const concepts: ConceptResult[] = [
  { concept: 'Data types', questions_attempted: 2, correct_answers: 2, accuracy: 1, proficiency_level: 'strong', identified_gap: false },
  { concept: 'Async', questions_attempted: 3, correct_answers: 0, accuracy: 0, proficiency_level: 'needs_improvement', identified_gap: true },
  { concept: 'OOP', questions_attempted: 1, correct_answers: 0, accuracy: 0, proficiency_level: 'unknown', identified_gap: false },
]
const gaps = (o: Partial<SkillGapReport> = {}): SkillGapReport => ({ skill: 'Python', skill_id: 'py', overall_score: 40, overall_level: 'needs_improvement', questions: 6, strong_concepts: ['Data types'], weak_concepts: [], critical_gaps: ['Async'],
  unknown_concepts: ['OOP'], recommended_sections: [{ concept: 'Async', key: 'gap-async', priority: 'critical', accuracy: 0 }], recommended_learning_order: ['Async'], prerequisite_gaps: [{ skill_id: 'x', name: 'Functions', required: 2, current: null }],
  by_difficulty: [], concepts, caveat: 'A short assessment samples only part of a skill.', basis: 'deterministic', ...o })
const result = (o: Partial<AssessmentResults> = {}): AssessmentResults => ({ assessment_id: 'a', status: 'completed', skill_id: 'py', skill: 'Python', score: 0.4, score_pct: 40, correct_count: 2, incorrect_count: 4, total_questions: 6,
  time_taken_seconds: 125, proficiency_level: 'needs_improvement', difficulty_level: 'intermediate', results: [], proficiency: null, concepts, skill_gaps: gaps(),
  review: [{ question_id: 'q1', prompt: 'Which is immutable?', options: ['list', 'tuple'], selected: 0, correct_options: [1], is_correct: false, explanation: 'Tuples are immutable.', concept: 'Data types', difficulty: 1 }], ...o })
const mountView = (r = result()) => mount(AssessmentResultsView, { props: { result: r }, global: { stubs: { EChart: { props: ['option', 'label', 'summary'], template: '<div data-test="chart" :data-summary="summary" />' } } } })

describe('uuid', () => {
  it('is a v4 id and still works when crypto.randomUUID is unavailable (insecure origins)', () => {
    expect(uuid()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    const real = globalThis.crypto.randomUUID
    Object.defineProperty(globalThis.crypto, 'randomUUID', { value: undefined, configurable: true })
    try { const ids = new Set(Array.from({ length: 50 }, uuid)); expect(ids.size).toBe(50); for (const i of ids) expect(i).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4/) } finally { Object.defineProperty(globalThis.crypto, 'randomUUID', { value: real, configurable: true }) }
  })
})

describe('concept chart', () => {
  it('uses backend accuracy sorted weakest-first, greys out concepts with too little data, and is describable in text', () => {
    const o = conceptAccuracyOption(concepts)
    expect(o.yAxis.data).toEqual(['Async', 'OOP', 'Data types'].sort((a, b) => ({ Async: 0, OOP: 0.5, 'Data types': 1 } as any)[a] - ({ Async: 0, OOP: 0.5, 'Data types': 1 } as any)[b]))
    expect(o.series[0].data.map((d: any) => d.value)).toEqual([0, 0, 100]); expect(o.series[0].data[2].itemStyle.color).toBe('#15803d')
    expect(describeConcepts(concepts)).toContain('Async 0% (Needs improvement)'); expect(describeConcepts(concepts)).toContain('Data types 100% (Strong)')
    expect(LEVEL_LABEL.unknown).toBe('Not enough data'); expect(formatDuration(125)).toBe('2 min 5s'); expect(formatDuration(9)).toBe('9s'); expect(formatDuration(undefined)).toBe('—')
  })
})

describe('AssessmentResultsView', () => {
  it('shows summary, concept performance, gap analysis, learning order, prerequisites and the caveat', () => {
    const t = mountView().text()
    expect(t).toContain('Score (computed by the backend)'); expect(t).toContain('40%'); expect(t).toContain('2 min 5s'); expect(t).toContain('Correct'); expect(t).toContain('Incorrect')
    expect(t).toContain('Concept performance'); expect(t).toContain('Critical gaps'); expect(t).toContain('Async'); expect(t).toContain('Strong concepts'); expect(t).toContain('Not enough data')
    expect(t).toContain('Recommended learning order'); expect(t).toContain('Prerequisites to refresh'); expect(t).toContain('Functions'); expect(t).toContain('A short assessment samples only part of a skill.')
    expect(t).toContain('Your answer'); expect(t).toContain('Correct answer'); expect(t).toContain('Tuples are immutable.')
  })
  it('the plan button emits, is disabled when there is nothing to target, and becomes "Open" once a plan exists', async () => {
    const w = mountView()
    const cta = () => w.findAll('button').find((b) => b.text().includes('Generate My Personalized Learning Plan'))!
    await cta().trigger('click'); expect(w.emitted('plan')).toHaveLength(1)
    await w.findAll('button').find((b) => b.text().includes('Retake'))!.trigger('click'); expect(w.emitted('retake')).toHaveLength(1)
    const none = mountView(result({ skill_gaps: gaps({ critical_gaps: [], weak_concepts: [], recommended_learning_order: [], unknown_concepts: [], recommended_sections: [], prerequisite_gaps: [] }) }))
    expect(none.findAll('button').find((b) => b.text().includes('Generate My Personalized'))!.attributes('disabled')).toBeDefined()
    expect(none.text()).toContain('No gaps were found in this attempt')
    const linked = mountView(result({ learning_session_id: 's1' })); expect(linked.text()).toContain('Open my learning plan'); expect(linked.text()).not.toContain('Generate My Personalized')
    await linked.findAll('button').find((b) => b.text().includes('Open my learning plan'))!.trigger('click'); expect(linked.emitted('openPlan')).toHaveLength(1)
  })
  it('surfaces a plan error and an unanswered review item', () => {
    const w = mount(AssessmentResultsView, { props: { result: result({ review: [{ question_id: 'q', prompt: 'P?', options: ['a', 'b'], selected: null, correct_options: [0], is_correct: false, explanation: 'Because.', concept: 'X', difficulty: 2 }] }), planError: 'This assessment found no skill gaps.' },
      global: { stubs: { EChart: true } } })
    expect(w.get('[role=alert]').text()).toContain('no skill gaps'); expect(w.text()).toContain('Not answered.')
  })
})
