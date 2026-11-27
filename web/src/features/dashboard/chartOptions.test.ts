import { describe, expect, it } from 'vitest'
import { accuracyOption, describeAccuracy, describeProficiency, proficiencyBarsOption, weeklyActivityOption } from './chartOptions'
import { progressionOption, readinessTrendOption, retentionOption } from './analyticsOptions'
import type { AnalyticsSummary } from '@/types/api'

const base = (o: Partial<AnalyticsSummary> = {}): AnalyticsSummary => ({
  skills_assessed: 0, assessments_completed: 0, weekly_activity: [], accuracy_trend: [], proficiency_series: [],
  consistency: { active_days_last_28: 0, ratio: 0, current_streak_days: 0, basis: '' }, roadmap_completion: { done: 0, total: 0, ratio: null, roadmaps: [] },
  strongest: [], weakest: [], retention: [], learning_hours: null, unavailable: {}, career: null, insights: [], insights_basis: '', ...o })

describe('chart options use only backend data', () => {
  it('weekly activity maps weeks to bars/line without inventing values', () => {
    const o: any = weeklyActivityOption(base({ weekly_activity: [{ week_start: '2026-06-01', assessments: 2, questions: 9 }, { week_start: '2026-06-08', assessments: 0, questions: 0 }] }))
    expect(o.series[0].data).toEqual([2, 0]); expect(o.series[1].data).toEqual([9, 0])
  })
  it('accuracy is percent of backend score; empty input yields empty series', () => {
    const o: any = accuracyOption(base({ accuracy_trend: [{ at: '2026-06-01T00:00:00Z', score: 0.756, skill_id: 's', skill: 'Py' }] }))
    expect(o.series[0].data).toEqual([76]); expect((accuracyOption(base()) as any).series[0].data).toEqual([])
    expect(describeAccuracy(base())).toBe('No completed assessments.')
  })
  it('proficiency bars use the latest point per skill, sorted', () => {
    const s = base({ proficiency_series: [{ skill_id: 'a', skill: 'A', points: [{ at: 't', score: 1, confidence: 0 }, { at: 't2', score: 3.456, confidence: 0 }] }, { skill_id: 'b', skill: 'B', points: [{ at: 't', score: 2, confidence: 0 }] }] })
    const o: any = proficiencyBarsOption(s); expect(o.yAxis.data).toEqual(['B', 'A']); expect(o.series[0].data).toEqual([2, 3.46])
    expect(describeProficiency(s)).toContain('A 3.5 of 5')
  })
  it('large series are downsampled/zoomable', () => {
    const pts = Array.from({ length: 80 }, (_, i) => ({ at: `2026-01-${String((i % 28) + 1).padStart(2, '0')}`, score: 2, confidence: 1 }))
    const o: any = progressionOption(base({ proficiency_series: [{ skill_id: 'a', skill: 'A', points: pts }] }))
    expect(o.series[0].sampling).toBe('lttb'); expect(o.dataZoom).toBeTruthy()
  })
  it('readiness trend and retention', () => {
    const career = { readiness_trend: [{ date: '2026-06-01', readiness: 0.2, evidence_confidence: 0.1 }, { date: '2026-06-05', readiness: 0.5, evidence_confidence: 0.3 }] } as any
    expect((readinessTrendOption(base({ career })) as any).series[0].data).toEqual([20, 50])
    expect((retentionOption(base({ retention: [{ skill_id: 'a', skill: 'A', first: 1, last: 2, delta: 1, days_between: 5 }, { skill_id: 'b', skill: 'B', first: 3, last: 2, delta: -1, days_between: 5 }] })) as any).yAxis.data).toEqual(['B', 'A'])
  })
})
