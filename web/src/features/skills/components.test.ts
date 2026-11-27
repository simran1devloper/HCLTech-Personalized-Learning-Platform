import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory } from 'vue-router'
import { createAppRouter } from '@/app/router'
import CareerOverview from './CareerOverview.vue'
import CareerSummaryPanel from './CareerSummaryPanel.vue'
import SkillDetailPanel from './SkillDetailPanel.vue'
import SkillListView from './SkillListView.vue'
import SkillNode from './SkillNode.vue'
import StatusLegend from './StatusLegend.vue'
import { NODES, detail, graph, node } from './fixtures'
import { STATUS, STATUS_ORDER, categoryColor, hoursLabel, levelLabel } from './status'

const plugins = () => [createPinia(), [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }], createAppRouter(createMemoryHistory())]
// Vue Flow's <Handle> needs a live flow context; jsdom has no layout, so the virtual list is given a viewport height.
const m = (c: any, props: object) => mount(c, { props, global: { plugins: plugins() as any, stubs: { Handle: true } } })
Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 400 })

describe('SkillNode', () => {
  it('shows name, category, status, mastery and missing prerequisites', () => {
    const w = m(SkillNode, { data: { skill: NODES.find((n) => n.skill_id === 'rag')!, role: 'normal', unmet: false } })
    expect(w.text()).toContain('RAG'); expect(w.text()).toContain('Ai'); expect(w.text()).toContain('Needs revision'); expect(w.text()).toContain('80%'); expect(w.text()).toContain('4 prerequisites missing')
    expect(w.attributes('aria-label')).toMatch(/RAG, Ai, Needs revision, mastery 80%, 4 prerequisites missing/)
  })
  it.each(STATUS_ORDER)('renders the %s state', (status) => {
    const w = m(SkillNode, { data: { skill: node('x', { status }), role: 'normal', unmet: false } })
    expect(w.attributes('data-status')).toBe(status); expect(w.text()).toContain(STATUS[status].label)
  })
  it('dims unrelated nodes, flags unmet prerequisites, and is keyboard-selectable', async () => {
    const w = m(SkillNode, { data: { skill: node('x'), role: 'dimmed', unmet: true } })
    expect(w.classes()).toContain('opacity-30'); expect(w.classes().join(' ')).toContain('border-dashed')
    await w.trigger('keydown.enter'); await w.trigger('keydown.space')
    expect(w.emitted('select')).toEqual([['x'], ['x']]); expect(w.attributes('tabindex')).toBe('0')
  })
  it('shows study progress separately from mastery when nothing is assessed', () => {
    const w = m(SkillNode, { data: { skill: node('x', { learning_progress: 0.5, status: 'in_progress' }), role: 'normal', unmet: false } })
    expect(w.text()).toContain('Studied 50% · not assessed')
  })
})

describe('status helpers', () => {
  it('category colour is stable; levels and hours are labelled', () => {
    expect(categoryColor('ai')).toBe(categoryColor('ai')); expect(levelLabel(0)).toBe('Not assessed'); expect(levelLabel(1.5)).toBe('Beginner'); expect(levelLabel(3)).toBe('Intermediate'); expect(levelLabel(4.5)).toBe('Expert')
    expect(hoursLabel(0)).toBe('—'); expect(hoursLabel(14.2)).toBe('~14 hrs')
  })
})

describe('CareerOverview', () => {
  it('shows readiness, coverage, mastery, mastered, in progress and remaining as separate metrics', () => {
    const w = m(CareerOverview, { metrics: graph().metrics, title: 'AI Engineer' })
    const t = w.text()
    expect(t).toContain('42%'); expect(t).toContain('AI Engineer'); expect(t).toContain('4 / 7'); expect(t).toContain('57% assessed'); expect(t).toContain('33%'); expect(t).toContain('1 need revision')
    for (const k of ['coverage', 'mastery', 'mastered', 'learning', 'remaining']) expect(w.find(`[data-metric=${k}]`).exists()).toBe(true)
    expect(w.get('[role=img]').attributes('aria-label')).toBe('Career readiness 42%')
  })
  it('shows skeletons while loading', () => { expect(m(CareerOverview, { loading: true }).findAll('[data-metric]')).toHaveLength(0) })
})

describe('StatusLegend', () => {
  it('toggles status filters and shows counts', async () => {
    const w = m(StatusLegend, { active: ['ready'], counts: { mastered: 3 } })
    expect(w.findAll('button')).toHaveLength(6); expect(w.text()).toContain('Mastered3')
    expect(w.findAll('button').find((b) => b.text().startsWith('Ready'))!.attributes('aria-pressed')).toBe('true')
    await w.findAll('button')[0].trigger('click'); expect(w.emitted('toggle')![0]).toEqual(['mastered'])
  })
})

describe('SkillListView', () => {
  it('lists skills with status and selects on click', async () => {
    const w = m(SkillListView, { nodes: NODES, selectedId: 'py' }); await flushPromises()
    expect(w.text()).toContain('Python'); expect(w.text()).toContain('Mastered'); expect(w.text()).toContain('critical')
    expect(w.get('[data-skill=py]').attributes('aria-pressed')).toBe('true')
    await w.get('[data-skill=sql]').trigger('click'); expect(w.emitted('select')![0]).toEqual(['sql'])
  })
  it('renders only a window of a very large list (virtualisation)', async () => {
    const big = Array.from({ length: 5000 }, (_, i) => node(`n${i}`))
    const w = m(SkillListView, { nodes: big, selectedId: null }); await flushPromises()
    const rows = w.findAll('[role=listitem]').length
    expect(rows).toBeGreaterThan(0); expect(rows).toBeLessThan(100)
  })
  it('shows an empty state', () => { expect(m(SkillListView, { nodes: [], selectedId: null }).text()).toContain('No skills match') })
})

describe('SkillDetailPanel', () => {
  const props = (id = 'llm', o: object = {}) => ({ node: NODES.find((n) => n.skill_id === id)!, detail: detail(id), pending: false, focused: false, ...o })
  it('shows mastery, level, difficulty, time, prerequisites (met/missing), unlocks and evidence', () => {
    const t = m(SkillDetailPanel, props()).text()
    expect(t).toContain('LLM Fundamentals'); expect(t).toContain('Blocked'); expect(t).toContain('20%'); expect(t).toContain('Beginner'); expect(t).toContain('1.0 / 3.5'); expect(t).toContain('Difficulty')
    expect(t).toContain('~16 hrs'); expect(t).toContain('Prerequisites missing'); expect(t).toContain('Machine Learning'); expect(t).toContain('RAG'); expect(t).toContain('Prompt Engineering')
    expect(t).toContain('quiz'); expect(t).toContain('50% @ d3'); expect(t).toContain('LLM basics'); expect(t).toContain('2/5 sections')
  })
  it('emits the five actions with the skill id', async () => {
    const w = m(SkillDetailPanel, props())
    const click = (label: string) => w.findAll('button').find((b) => b.text().includes(label))!.trigger('click')
    await click('Continue learning'); await click('Take assessment'); await click('Revise skill'); await click('Related skills')
    expect(w.emitted('start')![0]).toEqual(['llm']); expect(w.emitted('assess')![0]).toEqual(['llm']); expect(w.emitted('revise')![0]).toEqual(['llm']); expect(w.emitted('focus')![0]).toEqual([true])
  })
  it('first-time skills say "Start learning"; Revise is disabled when there is nothing to revise', () => {
    const w = m(SkillDetailPanel, props('pg', { detail: detail('llm') }))
    expect(w.text()).toContain('Start learning'); expect(w.findAll('button').find((b) => b.text().includes('Revise skill'))!.attributes('disabled')).toBeDefined()
  })
  it('selecting a prerequisite or an unlocked skill navigates the graph', async () => {
    const w = m(SkillDetailPanel, props())
    await w.findAll('button').find((b) => b.text() === 'RAG')!.trigger('click'); expect(w.emitted('select')!.at(-1)).toEqual(['rag'])
  })
  it('explains staleness and keeps study progress separate from mastery', () => {
    const w = m(SkillDetailPanel, props('rag', { node: node('rag', { name: 'RAG', status: 'needs_revision', revision_reason: 'stale', learning_progress: 0.4, evidence_count: 2, mastery: 0.8, proficiency: 3.6 }), detail: detail('rag') }))
    expect(w.text()).toContain('more than 90 days old'); expect(w.text()).toContain('Study progress'); expect(w.text()).toContain('does not count as mastery')
  })
  it('shows a loading skeleton and an error with retry for the on-demand detail', async () => {
    expect(m(SkillDetailPanel, props('llm', { detail: undefined, pending: true })).text()).not.toContain('Prerequisites\n')
    const w = m(SkillDetailPanel, props('llm', { detail: undefined, pending: false, error: 'boom' })); expect(w.text()).toContain('boom')
  })
})

describe('CareerSummaryPanel (nothing selected)', () => {
  it('shows summary, next milestone, critical gaps and recommendations with actions', async () => {
    const w = m(CareerSummaryPanel, { graph: graph() })
    const t = w.text()
    expect(t).toContain('AI Engineer'); expect(t).toContain('1 of 7 skills mastered'); expect(t).toContain('Next milestone'); expect(t).toContain('Critical skill gaps'); expect(t).toContain('Recommended next skills')
    expect(t).toContain('1. Machine Learning'); expect(t).toContain('high'); expect(t).toContain('Missing: Machine Learning')
    const btns = w.findAll('[data-rec] button').filter((b) => /^(Start learning|Learn prerequisites first)/.test(b.text()))
    await btns[0].trigger('click'); await btns[1].trigger('click')
    expect(w.emitted('act')![0]).toEqual(['ml', 'start_learning']); expect(w.emitted('act')![1]).toEqual(['ml', 'learn_prerequisites'])   // blocked skill routes to its first prerequisite
  })
  it('congratulates when every requirement is mastered', () => {
    const done = graph({ nodes: NODES.map((n) => ({ ...n, status: 'mastered' as const })), recommendations: [] })
    expect(m(CareerSummaryPanel, { graph: done }).text()).toContain('Every requirement of this career is mastered')
  })
})
