import { describe, expect, it, vi } from 'vitest'
import DashboardPage from './DashboardPage.vue'
import { mountApp, flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import type { AnalyticsSummary } from '@/types/api'
import { useAuthStore } from '@/stores/auth'

const summary = (o: Partial<AnalyticsSummary> = {}): AnalyticsSummary => ({
  skills_assessed: 3, assessments_completed: 4,
  weekly_activity: [{ week_start: '2026-06-01', assessments: 2, questions: 8 }],
  accuracy_trend: [{ at: '2026-06-02T10:00:00Z', score: 0.8, skill_id: 'p', skill: 'Python' }],
  proficiency_series: [{ skill_id: 'p', skill: 'Python', points: [{ at: '2026-06-02', score: 3.2, confidence: 0.6 }] }],
  consistency: { active_days_last_28: 5, ratio: 0.18, current_streak_days: 2, basis: 'days with at least one completed assessment' },
  roadmap_completion: { done: 1, total: 4, ratio: 0.25, roadmaps: [] }, strongest: [], weakest: [], retention: [], learning_hours: null,
  unavailable: { learning_hours: 'Study time is not tracked by the backend yet.' },
  career: { readiness_trend: [], role_slug: 'ai-engineer', title: 'AI Engineer', readiness: 0.42, evidence_confidence: 0.3, critical_gaps: [], unassessed: [], basis: 'x',
    gap_summary: { open_gaps: 2, blocked: 1, top: [{ skill_id: 'r', name: 'RAG', current: 1, target: 3.5, gap: 2.5, priority: 0.5, blocked: true }] }, recommended_next: { skill_id: 'x', name: 'Prompt Engineering' } },
  insights: [{ kind: 'streak', text: '2-day assessment streak.' }], insights_basis: 'rule-based observations', ...o })

const setup = async (s: Promise<AnalyticsSummary> | AnalyticsSummary) => {
  vi.spyOn(api.analyticsApi, 'summary').mockImplementation(() => Promise.resolve(s) as any)
  vi.spyOn(api.assessmentApi, 'list').mockResolvedValue({ items: [{ assessment_id: 'a1', skill_id: 'p', skill: 'Python', status: 'completed', started_at: new Date().toISOString(), completed_at: new Date().toISOString(), n_questions: 3, score: 0.8 }], total: 1, offset: 0, limit: 5 })
  vi.spyOn(api.careerApi, 'roles').mockResolvedValue([])
  const r = await mountApp(DashboardPage); useAuthStore().$patch({ user: { id: 'u', name: 'Asha Kumar', email: 'a@e.com' } }); await flushPromises(); return r
}

describe('Dashboard', () => {
  it('renders backend numbers exactly and labels unavailable metrics instead of inventing them', async () => {
    const { wrapper } = await setup(summary())
    const t = wrapper.text()
    expect(t).toContain('42%'); expect(t).toContain('AI Engineer'); expect(t).toContain('25%'); expect(t).toContain('1 of 4 milestones'); expect(t).toContain('2 d')
    expect(t).toContain('Not available: Study time is not tracked by the backend yet.')
    expect(t).toContain('Prompt Engineering'); expect(t).toContain('RAG'); expect(t).toContain('Blocked by prerequisites'); expect(t).toContain('2-day assessment streak.')
    expect(wrapper.findAll('[data-test=chart]').length).toBeGreaterThanOrEqual(3)
    expect(wrapper.find('[data-label="Weekly activity chart"]').attributes('data-summary')).toContain('2 in total')
  })
  it('shows empty/CTA states for a new learner (no goal, no data) — no fake figures', async () => {
    const { wrapper } = await setup(summary({ skills_assessed: 0, assessments_completed: 0, proficiency_series: [], accuracy_trend: [], career: null, roadmap_completion: { done: 0, total: 0, ratio: null, roadmaps: [] }, insights: [{ kind: 'start', text: 'No assessments completed yet.' }] }))
    const t = wrapper.text()
    expect(t).toContain('Not available: set a career goal'); expect(t).toContain('Not available: no roadmap generated yet'); expect(t).toContain('No assessed skills yet'); expect(t).toContain('No goal set')
    expect(t).toContain('No recommendation yet'); expect(wrapper.find('[data-label="Skill proficiency chart"]').exists()).toBe(false)
    const ai = wrapper.findAll('button').find((b) => b.text().includes('AI summary'))!; expect(ai.attributes('disabled')).toBeDefined()
  })
  it('shows a loading skeleton, then an error state with a working retry', async () => {
    const spy = vi.spyOn(api.analyticsApi, 'summary').mockRejectedValueOnce(new ApiError('Unable to reach the server.', 0, 'network')).mockResolvedValue(summary())
    vi.spyOn(api.assessmentApi, 'list').mockResolvedValue({ items: [], total: 0, offset: 0, limit: 5 })
    const { wrapper } = await mountApp(DashboardPage)
    expect(wrapper.find('[role=alert]').text()).toContain('Unable to reach the server.')
    await wrapper.findAll('button').find((b) => b.text() === 'Try again')!.trigger('click'); await flushPromises()
    expect(spy).toHaveBeenCalledTimes(2); expect(wrapper.text()).toContain('42%')
  })
})
