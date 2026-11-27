import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
import SkillsPage from './SkillsPage.vue'
import { mountApp } from '@/test-utils'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import { detail, graph, node, NODES } from '@/features/skills/fixtures'
import { clearLayoutCache } from '@/features/skills/layout'
import type { CareerRole } from '@/types/api'

/** The Vue Flow canvas is replaced by a probe (jsdom has no layout); everything else is the real page, store, queries and router. */
const Canvas = defineComponent({
  props: ['nodes', 'edges', 'highlight', 'matches', 'fullscreen'], emits: ['select', 'toggle-fullscreen'],
  setup(p, { emit, expose }) {
    expose({ focusNode: vi.fn() })
    return () => h('div', { 'data-test': 'canvas', 'data-nodes': p.nodes.map((n: any) => n.skill_id).sort().join(','), 'data-selected': p.highlight.selected ?? '', 'data-up': [...p.highlight.up].sort().join(','), 'data-down': [...p.highlight.down].sort().join(','), 'data-unmet': [...p.highlight.unmet].sort().join(','), 'data-matches': p.matches ? [...p.matches].join(',') : '' },
      p.nodes.map((n: any) => h('button', { 'data-node': n.skill_id, onClick: () => emit('select', n.skill_id) }, n.name)))
  },
})
const roles: CareerRole[] = [{ slug: 'ai-engineer', title: 'AI Engineer', requirements: [] }, { slug: 'devops-engineer', title: 'DevOps Engineer', requirements: [] }]
const everything = () => graph({ role: null, nodes: [...NODES, node('k8s', { name: 'Kubernetes', category: 'devops' })], recommendations: [], metrics: { ...graph().metrics, skills_total: 8, readiness: 0 } })
const devops = () => graph({ role: { slug: 'devops-engineer', title: 'DevOps Engineer', description: '' }, nodes: NODES.slice(0, 3), edges: [], metrics: { ...graph().metrics, skills_total: 3, readiness: 0.1 }, recommendations: [] })

async function setup({ goal = 'ai-engineer' as string | null, route = '/skills', graphError = false } = {}) {
  const spies = {
    roles: vi.spyOn(api.careerApi, 'roles').mockResolvedValue(roles),
    goals: vi.spyOn(api.careerApi, 'goals').mockResolvedValue(goal ? [{ goal_id: 'g', role_slug: goal, hours_per_week: 6, created_at: 'x' }] : []),
    graph: vi.spyOn(api.explorerApi, 'graph').mockImplementation(async (r: string) => { if (graphError) throw new ApiError('Server exploded', 500, 'internal_error'); return r === 'devops-engineer' ? devops() : r === 'all' ? everything() : graph() }),
    detail: vi.spyOn(api.explorerApi, 'detail').mockImplementation(async (id: string) => detail(id)),
    createGoal: vi.spyOn(api.careerApi, 'createGoal').mockResolvedValue({ goal_id: 'g2', role_slug: 'devops-engineer', hours_per_week: 6, created_at: 'x' }),
  }
  // a realistic cache lifetime (the shared test client uses gcTime 0, which would hide whether revisits are served from cache)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 60_000 } } })
  const m = await mountApp(SkillsPage, { route, queryClient, global: { stubs: { SkillGraphView: Canvas } } }); await flushPromises()
  return { ...m, spies, canvas: () => m.wrapper.get('[data-test=canvas]'), nodes: () => m.wrapper.get('[data-test=canvas]').attributes('data-nodes')!.split(',').filter(Boolean) }
}
const click = async (w: any, sel: string) => { await w.get(sel).trigger('click'); await flushPromises() }
beforeEach(() => { localStorage.setItem('learnai_token', 't'); clearLayoutCache() })
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('Skill Explorer page', () => {
  it('loads the learner’s career graph in a single request and renders overview + summary', async () => {
    const { wrapper, spies } = await setup()
    expect(spies.graph).toHaveBeenCalledTimes(1); expect(spies.graph.mock.calls[0][0]).toBe('ai-engineer'); expect(spies.detail).not.toHaveBeenCalled()
    const t = wrapper.text()
    expect(t).toContain('Skill Explorer'); expect(t).toContain('42%'); expect(t).toContain('4 / 7'); expect(t).toContain('Next milestone'); expect(t).toContain('Recommended next skills'); expect(t).toContain('7 of 7 skills')
    expect((wrapper.get('select[aria-label="Target career"]').element as HTMLSelectElement).value).toBe('ai-engineer')
  })

  it('selecting a skill highlights its prerequisite path and downstream skills, loads detail on demand and updates the URL', async () => {
    const { wrapper, spies, canvas, router } = await setup()
    await click(wrapper, '[data-node=llm]')
    expect(canvas().attributes('data-selected')).toBe('llm'); expect(canvas().attributes('data-up')).toBe('ml,py'); expect(canvas().attributes('data-down')).toBe('prompt,rag'); expect(canvas().attributes('data-unmet')).toBe('ml')
    expect(spies.detail).toHaveBeenCalledTimes(1); expect(spies.detail.mock.calls[0].slice(0, 2)).toEqual(['llm', 'ai-engineer'])
    expect(wrapper.find('[data-test=skill-detail]').exists()).toBe(true); expect(wrapper.text()).toContain('Prerequisites missing'); expect(wrapper.find('[data-test=career-summary]').exists()).toBe(false)
    expect(router.currentRoute.value.query.skill).toBe('llm')
    await click(wrapper, 'button[aria-label="Clear selection"]')
    expect(canvas().attributes('data-selected')).toBe(''); expect(wrapper.find('[data-test=career-summary]').exists()).toBe(true); expect(router.currentRoute.value.query.skill).toBeUndefined()
  })

  it('accepts a skill slug in the URL as well as its id', async () => {
    const { canvas, router } = await setup({ route: '/skills?skill=llm' })
    expect(canvas().attributes('data-selected')).toBe('llm'); expect(router.currentRoute.value.query.skill).toBe('llm')
    const s = await setup({ route: '/skills?skill=prompt-engineering' }); expect(s.canvas().attributes('data-selected')).toBe('prompt')
  })

  it('opens directly on a shared link (?skill=&role=)', async () => {
    const { wrapper, spies, canvas } = await setup({ route: '/skills?skill=rag&role=devops-engineer' })
    expect(spies.graph.mock.calls[0][0]).toBe('devops-engineer')
    expect(canvas().attributes('data-selected')).toBe('')            // rag is not part of the DevOps graph fixture: a stale id is dropped, not shown
    expect(wrapper.find('[data-test=career-summary]').exists()).toBe(true)
  })

  it('switching career fetches the new mapping, recalculates metrics, clears the selection, and revisiting uses the cache', async () => {
    const { wrapper, spies, canvas } = await setup()
    await click(wrapper, '[data-node=llm]')
    await wrapper.get('select[aria-label="Target career"]').setValue('devops-engineer'); await flushPromises()
    expect(spies.graph).toHaveBeenCalledTimes(2); expect(spies.graph.mock.calls[1][0]).toBe('devops-engineer')
    expect(wrapper.text()).toContain('DevOps Engineer'); expect(wrapper.text()).toContain('10%'); expect(canvas().attributes('data-nodes')).toBe('ml,py,sql'); expect(canvas().attributes('data-selected')).toBe('')
    expect(wrapper.text()).toContain('Make this my goal')
    await wrapper.get('select[aria-label="Target career"]').setValue('ai-engineer'); await flushPromises()
    expect(spies.graph).toHaveBeenCalledTimes(2)                       // cached: no new request
    expect(wrapper.text()).toContain('42%'); expect(wrapper.text()).not.toContain('Make this my goal')
  })

  it('"Make this my goal" saves the explored career as the goal', async () => {
    const { wrapper, spies } = await setup()
    await wrapper.get('select[aria-label="Target career"]').setValue('devops-engineer'); await flushPromises()
    const b = wrapper.findAll('button').find((x) => x.text().includes('Make this my goal'))!; await b.trigger('click'); await flushPromises()
    expect(spies.createGoal).toHaveBeenCalledWith({ role_slug: 'devops-engineer', hours_per_week: 6 })
  })

  it('category and status filters change which skills are drawn; clear filters restores them', async () => {
    const { wrapper, nodes } = await setup()
    expect(nodes()).toHaveLength(7)
    await wrapper.get('select[aria-label="Filter by category"]').setValue('ai'); await flushPromises()
    expect(nodes().sort()).toEqual(['llm', 'prompt', 'rag'])
    await wrapper.get('select[aria-label="Filter by category"]').setValue(''); await flushPromises()
    await wrapper.findAll('[role=group][aria-label="Filter by status"] button').find((b) => b.text().startsWith('Mastered'))!.trigger('click'); await flushPromises()
    expect(nodes()).toEqual(['py'])
    await wrapper.findAll('[role=group][aria-label="Filter by status"] button').find((b) => b.text().startsWith('Blocked'))!.trigger('click'); await flushPromises()
    expect(nodes().sort()).toEqual(['llm', 'py'])
    expect(wrapper.get('[data-test=count]').text()).toContain('2 of 7 skills')
    await wrapper.findAll('button').find((b) => b.text().includes('Clear filters'))!.trigger('click'); await flushPromises()
    expect(nodes()).toHaveLength(7)
  })

  it('search dims non-matches in the graph and Enter selects and focuses the best match', async () => {
    const { wrapper, canvas, router } = await setup()
    const input = wrapper.get('input[aria-label="Search skills"]'); await input.setValue('postgre'); await new Promise((r) => setTimeout(r, 200)); await flushPromises()
    expect(canvas().attributes('data-matches')).toBe('pg'); expect(canvas().attributes('data-nodes')!.split(',')).toHaveLength(7)
    await input.trigger('keydown.enter'); await flushPromises()
    expect(canvas().attributes('data-selected')).toBe('pg'); expect(router.currentRoute.value.query.skill).toBe('pg')
  })

  it('a search with no match dims nothing and says so', async () => {
    const { wrapper, canvas } = await setup()
    await wrapper.get('input[aria-label="Search skills"]').setValue('zzzz'); await new Promise((r) => setTimeout(r, 250)); await flushPromises()
    expect(canvas().attributes('data-matches')).toBe(''); expect(wrapper.get('[data-test=count]').text()).toContain('no skill matches “zzzz”')
  })

  it('toggles between graph and list; the list honours filters and selection', async () => {
    const { wrapper } = await setup()
    await wrapper.findAll('[role=group][aria-label=View] button').find((b) => b.text().includes('List'))!.trigger('click'); await flushPromises()
    expect(wrapper.find('[data-test=canvas]').exists()).toBe(false); expect(wrapper.find('[aria-label=Skills]').exists()).toBe(true)
    await wrapper.get('select[aria-label="Filter by category"]').setValue('data'); await flushPromises()
    expect(wrapper.get('[data-test=count]').text()).toContain('2 of 7')
    await wrapper.get('[data-skill=sql]').trigger('click'); await flushPromises()
    expect(wrapper.find('[data-test=skill-detail]').text()).toContain('SQL')
  })

  it('"Related skills" focuses the graph on the selected skill’s neighbourhood', async () => {
    const { wrapper, nodes } = await setup()
    await click(wrapper, '[data-node=pg]')
    await wrapper.findAll('button').find((b) => b.text().includes('Related skills'))!.trigger('click'); await flushPromises()
    expect(nodes().sort()).toEqual(['pg', 'py', 'rag', 'sql'])
    await wrapper.findAll('button').find((b) => b.text().includes('Show all skills'))!.trigger('click'); await flushPromises()
    expect(nodes()).toHaveLength(7)
  })

  it('Start Learning opens the Diagnostic Study with the skill and career context; Revise and Assess route correctly', async () => {
    const { wrapper, router } = await setup()
    await click(wrapper, '[data-node=llm]')
    await wrapper.findAll('button').find((b) => b.text().includes('Continue learning'))!.trigger('click'); await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('study'))
    expect(router.currentRoute.value.query).toMatchObject({ skill: 'llm', topic: 'LLM Fundamentals', career: 'ai-engineer', intent: 'learn' })
    await router.push('/skills?skill=llm'); await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('Revise skill'))!.trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.query.intent).toBe('revise'))
    await router.push('/skills?skill=llm'); await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('Take assessment'))!.trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('assessments'))
    expect(router.currentRoute.value.query.skill).toBe('LLM Fundamentals')
  })

  it('recommendation actions go to the first missing prerequisite', async () => {
    const { wrapper, router } = await setup()
    await wrapper.findAll('[data-rec=llm] button').find((b) => b.text().startsWith('Learn prerequisites'))!.trigger('click'); await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('study'))
    expect(router.currentRoute.value.query).toMatchObject({ skill: 'ml', career: 'ai-engineer' })
  })

  it('without a career goal it shows every skill, asks for a career for readiness, and switches when one is chosen', async () => {
    const { wrapper, spies, nodes } = await setup({ goal: null })
    expect(spies.graph).toHaveBeenCalledTimes(1); expect(spies.graph.mock.calls[0][0]).toBe('all')
    expect(nodes()).toHaveLength(8); expect(wrapper.text()).toContain('Choose a career above to see your readiness'); expect(wrapper.text()).toContain('Every skill in the catalogue')
    expect(wrapper.find('[data-test=career-summary]').text()).not.toContain('Recommended next skills');
    await wrapper.get('select[aria-label="Target career"]').setValue('ai-engineer'); await flushPromises()
    expect(spies.graph).toHaveBeenCalledTimes(2); expect(wrapper.text()).toContain('42%'); expect(wrapper.text()).toContain('Make this my goal')   // no goal yet: offer to save it
  })

  it('a deep link to a skill outside the learner’s career (command palette) falls back to the all-skills view instead of dropping it', async () => {
    const { wrapper, spies, canvas } = await setup({ route: '/skills?skill=k8s' })
    await flushPromises()
    expect(spies.graph.mock.calls.map((c) => c[0])).toEqual(['ai-engineer', 'all'])
    expect(canvas().attributes('data-selected')).toBe('k8s'); expect(wrapper.find('[data-test=skill-detail]').exists()).toBe(true)
    expect(spies.detail.mock.calls[0][0]).toBe('k8s')
  })

  it('does not fetch anything until the goal is known (no flash of the wrong graph)', async () => {
    let release!: (v: any) => void
    const spies = { roles: vi.spyOn(api.careerApi, 'roles').mockResolvedValue(roles), goals: vi.spyOn(api.careerApi, 'goals').mockReturnValue(new Promise((r) => (release = r))), graph: vi.spyOn(api.explorerApi, 'graph').mockResolvedValue(graph()) }
    const { wrapper } = await mountApp(SkillsPage, { route: '/skills', global: { stubs: { SkillGraphView: Canvas } } }); await flushPromises()
    expect(spies.graph).not.toHaveBeenCalled()
    release([{ goal_id: 'g', role_slug: 'ai-engineer', hours_per_week: 5, created_at: 'x' }]); await flushPromises()
    expect(spies.graph.mock.calls.map((c) => c[0])).toEqual(['ai-engineer']); expect(wrapper.text()).toContain('42%')
  })

  it('shows an error with retry when the graph cannot be loaded', async () => {
    const { wrapper, spies } = await setup({ graphError: true })
    expect(wrapper.text()).toContain('Server exploded')
    spies.graph.mockResolvedValue(graph())
    await wrapper.findAll('button').find((b) => /retry|try again/i.test(b.text()))!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('42%')
  })

  it('selection survives an assessment refresh of the same career graph (progress updates in place)', async () => {
    const { wrapper, spies, queryClient, canvas } = await setup()
    await click(wrapper, '[data-node=sql]')
    const updated = graph({ nodes: NODES.map((n) => (n.skill_id === 'sql' ? { ...n, status: 'mastered' as const, mastery: 0.95 } : n)) })
    spies.graph.mockResolvedValue(updated); await queryClient.invalidateQueries({ queryKey: ['career-graph'] }); await flushPromises()
    expect(canvas().attributes('data-selected')).toBe('sql'); expect(wrapper.find('[data-test=skill-detail]').text()).toContain('Mastered')
  })
})
