import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import HistoryPage from './HistoryPage.vue'
import HistorySessionPage from './HistorySessionPage.vue'
import { mountApp, flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import { NAV } from '@/composables/useNav'
import { sectionStatus, sessionStatus, progressText, contentTypeLabel } from '@/features/history/historyUi'
import type { ContentSessionDetail, ContentSessionSummary } from '@/types/api'

const now = new Date().toISOString()
const item = (o: Partial<ContentSessionSummary> = {}): ContentSessionSummary => ({
  id: 's1', title: 'Machine Learning Fundamentals', description: 'Study guide for ML', topic: 'Machine Learning', content_type: 'study_guide', learning_level: 'Intermediate', language: 'en',
  status: 'active', total_sections: 9, completed_sections: 8, created_at: now, updated_at: now, last_accessed_at: null, archived_at: null, tags: ['ml'], ...o })
const page = (items: ContentSessionSummary[]) => ({ items, total: items.length, offset: 0, limit: 12 })
const stubs = { MarkdownView: { props: ['source'], template: '<div data-test="md">{{ source }}</div>' }, FlashcardDeck: { template: '<div data-test="cards" />' } }
const KEYS = ['summary', 'key_concepts', 'deep_dive', 'real_world_applications', 'viva_voice', 'practical_guide', 'learning_roadmap', 'flashcards', 'recommendations']
const detail = (o: Partial<ContentSessionDetail> = {}, missing: string[] = []): ContentSessionDetail => ({
  ...item(), metadata: {}, active_jobs: [],
  sections: KEYS.map((k) => ({ section_key: k, section_title: k, status: missing.includes(k) ? 'NOT_STARTED' : 'COMPLETED', content_version: missing.includes(k) ? 0 : 1, has_content: !missing.includes(k), updated_at: now })), ...o })
const contentOf = (missing: string[] = []) => ({ session_id: 's1', topic: 'ML', level: 'Intermediate', incomplete_sections: missing,
  sections: Object.fromEntries(KEYS.filter((k) => !missing.includes(k)).map((k) => [k, k === 'key_concepts' ? [{ term: 'Gradient', definition: 'slope' }] : k === 'flashcards' ? [{ question: 'q', answer: 'a' }] : `## ${k} saved text`])) })

beforeEach(() => localStorage.setItem('learnai_token', 'test-token')) // the router guard only lets signed-in users reach these routes
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('navigation and helpers', () => {
  it('has a Content History entry between AI Tutor and Analytics', () => {
    const names = NAV.map((n) => n.label)
    expect(names).toEqual(['Dashboard', 'Skill Explorer', 'Roadmap', 'Assessments', 'Diagnostic Study', 'AI Tutor', 'Content History', 'Analytics'])
    expect(NAV.find((n) => n.name === 'history')?.icon).toBeTruthy()
  })
  it('labels statuses, progress and content types', () => {
    expect(progressText(8, 9)).toBe('8/9 sections completed'); expect(sessionStatus('failed').tone).toBe('danger'); expect(sectionStatus('NOT_STARTED').label).toBe('Not generated')
    expect(contentTypeLabel('technical_deep_dive')).toBe('Technical Deep Dive'); expect(contentTypeLabel('nope')).toBe('Other generated content')
  })
})

describe('History list', () => {
  it('shows each saved session with its progress, type, level and status — no generation calls', async () => {
    const list = vi.spyOn(api.historyApi, 'list').mockResolvedValue(page([item(), item({ id: 's2', title: 'REST API Design', learning_level: 'Advanced', content_type: 'technical_deep_dive', status: 'completed', completed_sections: 9 })]))
    const { wrapper } = await mountApp(HistoryPage)
    const t = wrapper.text()
    expect(t).toContain('My Learning History'); expect(t).toContain('Access and continue your previously generated learning content.')
    expect(t).toContain('Machine Learning Fundamentals'); expect(t).toContain('8/9 sections completed'); expect(t).toContain('Intermediate · Study Guide'); expect(t).toContain('In progress')
    expect(t).toContain('REST API Design'); expect(t).toContain('Advanced · Technical Deep Dive'); expect(t).toContain('9/9 sections completed'); expect(t).toContain('Completed')
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ status: 'all', sort: 'updated', offset: 0, limit: 12 }), expect.anything())
  })
  it('debounces search and sends it to the server together with filters and sort', async () => {
    const list = vi.spyOn(api.historyApi, 'list').mockResolvedValue(page([item()]))
    const { wrapper } = await mountApp(HistoryPage)
    list.mockClear()
    const box = wrapper.get('input[type=search]')
    await box.setValue('m'); await box.setValue('ml'); await box.setValue('mach')
    await flushPromises(); expect(list).not.toHaveBeenCalled()                       // still typing
    await new Promise((r) => setTimeout(r, 350)); await flushPromises()
    expect(list).toHaveBeenCalledTimes(1); expect(list.mock.calls[0][0]).toMatchObject({ q: 'mach', offset: 0 })
    await wrapper.get('select[aria-label="Filter by status"]').setValue('failed'); await wrapper.get('select[aria-label="Sort by"]').setValue('alphabetical'); await flushPromises()
    expect(list.mock.lastCall![0]).toMatchObject({ q: 'mach', status: 'failed', sort: 'alphabetical' })
  })
  it('renders an empty state for a new learner, a no-match state for a search, and a retryable error state', async () => {
    vi.spyOn(api.historyApi, 'list').mockResolvedValue(page([]))
    let m = await mountApp(HistoryPage)
    expect(m.wrapper.text()).toContain('No saved content yet'); expect(m.wrapper.text()).toContain('Generate your first study guide')
    await m.wrapper.get('input[type=search]').setValue('zzz'); await new Promise((r) => setTimeout(r, 350)); await flushPromises()
    expect(m.wrapper.text()).toContain('Nothing matches')
    vi.restoreAllMocks(); vi.spyOn(api.historyApi, 'list').mockRejectedValue(new ApiError('The server had a problem.', 500))
    m = await mountApp(HistoryPage); await flushPromises()
    expect(m.wrapper.find('[role=alert]').text()).toContain('The server had a problem.'); expect(m.wrapper.text()).toContain('Try again')
  })
  it('paginates on the server', async () => {
    const list = vi.spyOn(api.historyApi, 'list').mockResolvedValue({ items: [item()], total: 30, offset: 0, limit: 12 })
    const { wrapper } = await mountApp(HistoryPage)
    expect(wrapper.text()).toContain('1–12 of 30')
    await wrapper.findAll('button').find((b) => b.text() === 'Next')!.trigger('click'); await flushPromises()
    expect(list.mock.lastCall![0]).toMatchObject({ offset: 12 })
  })
})

describe('Saved session', () => {
  it('restores persisted content and section statuses without calling any generation endpoint', async () => {
    vi.spyOn(api.historyApi, 'get').mockResolvedValue(detail({ status: 'completed', completed_sections: 9 }))
    vi.spyOn(api.historyApi, 'content').mockResolvedValue(contentOf())
    const fetchSpy = vi.fn(); vi.stubGlobal('fetch', fetchSpy)
    const { wrapper } = await mountApp(HistorySessionPage, { route: '/history/s1', global: { stubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('Machine Learning Fundamentals'); expect(wrapper.text()).toContain('summary saved text'); expect(wrapper.text()).toContain('Version 1')
    expect(wrapper.find('[aria-label="Section status"]').text()).toContain('Ready')
    expect(fetchSpy).not.toHaveBeenCalled()                                         // reopening never streams/generates
  })
  it('shows missing sections with an on-demand Generate action and does not generate by itself', async () => {
    vi.spyOn(api.historyApi, 'get').mockResolvedValue(detail({ completed_sections: 8 }, ['deep_dive']))
    vi.spyOn(api.historyApi, 'content').mockResolvedValue(contentOf(['deep_dive']))
    const fetchSpy = vi.fn(); vi.stubGlobal('fetch', fetchSpy)
    const { wrapper } = await mountApp(HistorySessionPage, { route: '/history/s1', global: { stubs } })
    await flushPromises()
    expect(wrapper.find('[aria-label="Section status"]').text()).toContain('Not generated'); expect(wrapper.text()).toContain('8/9 sections completed')
    expect(wrapper.text()).toContain('Continue learning'); expect(fetchSpy).not.toHaveBeenCalled()
  })
  it('Continue learning resumes via the server and renders sections as they are persisted', async () => {
    vi.spyOn(api.historyApi, 'get').mockResolvedValue(detail({ completed_sections: 8 }, ['deep_dive']))
    vi.spyOn(api.historyApi, 'content').mockResolvedValue(contentOf(['deep_dive']))
    const body = [['meta', { sections: ['deep_dive'], generating: ['deep_dive'] }], ['section', { name: 'deep_dive', value: '## brand new deep dive', ok: true }], ['done', { incomplete_sections: [] }]]
      .map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
    const f = vi.fn(async () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(body)); c.close() } }), { status: 200 })); vi.stubGlobal('fetch', f)
    const { wrapper } = await mountApp(HistorySessionPage, { route: '/history/s1', global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('Continue learning'))!.trigger('click'); await flushPromises()
    expect((f.mock.calls[0] as any)[0]).toContain('/api/v1/content-sessions/s1/continue')
    expect(JSON.parse((f.mock.calls[0] as any)[1].body)).toEqual({})
  })
  it('a deleted or foreign session is a clear not-available state, not a crash', async () => {
    vi.spyOn(api.historyApi, 'get').mockRejectedValue(new ApiError('content session not found', 404, 'not_found'))
    vi.spyOn(api.historyApi, 'content').mockRejectedValue(new ApiError('content session not found', 404, 'not_found'))
    const { wrapper } = await mountApp(HistorySessionPage, { route: '/history/gone', global: { stubs } })
    await flushPromises(); await new Promise((r) => setTimeout(r, 20)); await flushPromises()
    expect(wrapper.text()).toContain('This content is no longer available')
  })
})
