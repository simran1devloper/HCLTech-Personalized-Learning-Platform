import { beforeEach, describe, expect, it, vi } from 'vitest'
import TutorPage from './TutorPage.vue'
import ToolCard from '@/features/tutor/ToolCard.vue'
import AssistantMessage from '@/features/tutor/AssistantMessage.vue'
import { groupConversations } from '@/features/tutor/grouping'
import { mountApp, flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import type { TutorConversation, TutorMessage, TutorTool } from '@/types/api'

const now = new Date().toISOString()
const conv = (o: Partial<TutorConversation> = {}): TutorConversation => ({ conversation_id: 'c1', title: 'Explain Redis', skill_id: null, language: 'en', created_at: now, updated_at: now, pinned: false, preview: 'Redis is…', ...o })
const reply = (o: Partial<TutorMessage> = {}): TutorMessage => ({ role: 'assistant', content: 'Redis is a data store.', created_at: now, message_id: 2, status: 'complete', tools: [], meta: {}, ...o })
const stubs = { MarkdownView: { props: ['source'], template: '<div data-test="md">{{ source }}</div>' } }
const sse = (events: [string, object][]) => new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(''), { status: 200, headers: { 'content-type': 'text/event-stream' } })
const click = async (w: any, text: string) => { await w.findAll('button').find((b: any) => b.text().includes(text))!.trigger('click'); await flushPromises() }
async function until(check: () => boolean) { for (let i = 0; i < 80 && !check(); i++) { await new Promise((r) => setTimeout(r, 10)); await flushPromises() } expect(check()).toBe(true) }

beforeEach(() => {
  localStorage.setItem('learnai_token', 't')
  vi.spyOn(api.careerApi, 'roles').mockResolvedValue([{ slug: 'data-engineer', title: 'Data Engineer', requirements: [] }] as any)
  vi.spyOn(api.careerApi, 'goals').mockResolvedValue([{ goal_id: 'g', role_slug: 'data-engineer', hours_per_week: 5, created_at: now }] as any)
  vi.spyOn(api.tutorApi, 'conversations').mockResolvedValue({ items: [] })
})

describe('conversation grouping', () => {
  it('puts pinned first, then by calendar day', () => {
    const base = new Date(2026, 5, 20, 12)
    const at = (d: number) => new Date(2026, 5, 20 - d, 9).toISOString()
    const g = groupConversations([conv({ conversation_id: 'a', updated_at: at(0) }), conv({ conversation_id: 'b', updated_at: at(1) }), conv({ conversation_id: 'c', updated_at: at(5) }),
      conv({ conversation_id: 'd', updated_at: at(40) }), conv({ conversation_id: 'e', updated_at: at(40), pinned: true })], base)
    expect(g.map((x) => [x.label, x.items[0].conversation_id])).toEqual([['Pinned', 'e'], ['Today', 'a'], ['Yesterday', 'b'], ['Previous 7 days', 'c'], ['Older', 'd']])
  })
})

describe('Tutor page', () => {
  it('shows the welcome screen, suggested prompts and the learner goal', async () => {
    const { wrapper } = await mountApp(TutorPage, { global: { stubs } })
    expect(wrapper.text()).toContain('What would you like to learn today?')
    for (const p of ['Explain a technical concept', 'Help me write or debug code', 'Test my knowledge', 'Create a learning roadmap', 'Prepare me for an interview']) expect(wrapper.text()).toContain(p)
    expect(wrapper.text()).toContain('My goal: Data Engineer')
  })
  it('a prompt that needs input only prefills the box', async () => {
    const f = vi.fn(); vi.stubGlobal('fetch', f)
    const { wrapper } = await mountApp(TutorPage, { global: { stubs } })
    await click(wrapper, 'Help me write or debug code')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toContain('Help me debug'); expect(f).not.toHaveBeenCalled()
  })
  it('sends the selected options, then shows what the server stored exactly once', async () => {
    const saved: TutorMessage[] = [{ role: 'user', content: 'Explain Redis', created_at: now, message_id: 1 }, reply({ meta: { followups: ['How does Redis work internally?'], sources: [{ kind: 'goal', label: 'Career goal', link: '/' }] } })]
    vi.spyOn(api.tutorApi, 'conversation').mockResolvedValue({ ...conv(), messages: saved })
    const f = vi.fn(async () => sse([['meta', { conversation_id: 'c1', message_id: 2 }], ['status', { stage: 'generating' }], ['token', { text: 'Redis is a data store.' }], ['done', {}]])); vi.stubGlobal('fetch', f)
    const { wrapper } = await mountApp(TutorPage, { global: { stubs } })
    await wrapper.find('textarea').setValue('Explain Redis'); await wrapper.find('form').trigger('submit')
    await until(() => wrapper.text().includes('Used your:'))
    const body = JSON.parse((f.mock.calls[0] as any)[1].body)
    expect(body).toMatchObject({ message: 'Explain Redis', mode: 'auto', level: 'auto', language: 'en', conversation_id: null }); expect(body.client_message_id.length).toBeGreaterThanOrEqual(8)
    expect(wrapper.findAll('[data-test="md"]')).toHaveLength(1); expect(wrapper.text()).toContain('How does Redis work internally?')
  })
  it('retry reuses the same message id and keeps the question visible when the server was not reached', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'internal_error', message: 'server problem' } }), { status: 500 })); vi.stubGlobal('fetch', f)
    const { wrapper } = await mountApp(TutorPage, { global: { stubs } })
    await wrapper.find('textarea').setValue('Explain Kafka'); await wrapper.find('form').trigger('submit')
    await until(() => wrapper.find('[role="alert"]').exists())
    expect(wrapper.text()).toContain('Explain Kafka'); expect(wrapper.text()).toContain('server problem')
    await click(wrapper, 'Retry'); await until(() => f.mock.calls.length === 2 && wrapper.find('[role="alert"]').exists())
    const ids = (f.mock.calls as any[]).map((c) => JSON.parse(c[1].body).client_message_id)
    expect(ids[0]).toBe(ids[1])
  })
  it('opens a stored conversation and explains an incomplete answer', async () => {
    vi.spyOn(api.tutorApi, 'conversations').mockResolvedValue({ items: [conv()] })
    vi.spyOn(api.tutorApi, 'conversation').mockResolvedValue({ ...conv(), messages: [{ role: 'user', content: 'Explain Redis', created_at: now, message_id: 1 }, reply({ status: 'cancelled', content: 'Red' })] })
    const { wrapper } = await mountApp(TutorPage, { global: { stubs } })
    await click(wrapper, 'Explain Redis')
    await until(() => wrapper.find('[data-testid="reply-note"]').exists())
    expect(wrapper.text()).toContain('Stopped before the answer finished.'); expect(wrapper.text()).toContain('Regenerate')
  })
})

describe('tool card and message actions', () => {
  const pending: TutorTool = { execution_id: 'e1', message_id: 2, tool: 'create_quiz', status: 'pending', args: { skill_id: 's', n_questions: 5 }, result: null }
  it('runs a proposed action only after Confirm, and shows its result', async () => {
    const confirm = vi.spyOn(api.tutorApi, 'confirm').mockResolvedValue({ ...pending, status: 'succeeded', result: { kind: 'quiz', title: 'Python quiz ready', data: { total_questions: 5, generating: false }, link: '/assessments/a1', link_label: 'Start quiz', empty: null } })
    const { wrapper } = await mountApp(ToolCard, { props: { tool: pending } })
    expect(wrapper.text()).toContain('Create a 5-question quiz'); expect(confirm).not.toHaveBeenCalled()
    await click(wrapper, 'Confirm')
    expect(confirm).toHaveBeenCalledWith('e1'); expect(wrapper.text()).toContain('Python quiz ready'); expect(wrapper.find('a').attributes('href')).toBe('/assessments/a1')
  })
  it('renders skill gaps from the tool data', async () => {
    const tool: TutorTool = { ...pending, tool: 'analyze_skill_gaps', status: 'succeeded', result: { kind: 'skill_gaps', title: 'Skill gaps for AI Engineer', link: '/skills', link_label: 'Open Skill Explorer', empty: null,
      data: { recommended_next: 'Python', items: [{ name: 'Python', current: null, assessed: false, target: 4, gap: 4, confidence: 0, blocked: false }] } } }
    const { wrapper } = await mountApp(ToolCard, { props: { tool } })
    expect(wrapper.find('table').text()).toContain('not assessed'); expect(wrapper.text()).toContain('Recommended next: Python')
  })
  it('toggles feedback and restores it when saving fails', async () => {
    const f = vi.spyOn(api.tutorApi, 'feedback').mockResolvedValueOnce({ rating: 'up' }).mockRejectedValueOnce(new Error('offline'))
    const { wrapper } = await mountApp(AssistantMessage, { props: { message: reply() }, global: { stubs } })
    const up = wrapper.find('[aria-label="Good response"]')
    await up.trigger('click'); await flushPromises(); expect(up.attributes('aria-pressed')).toBe('true'); expect(f).toHaveBeenLastCalledWith(2, 'up')
    await wrapper.find('[aria-label="Bad response"]').trigger('click'); await flushPromises()
    expect(up.attributes('aria-pressed')).toBe('true')
  })
})
