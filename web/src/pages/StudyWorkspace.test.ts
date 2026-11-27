import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StudyWorkspacePage from './StudyWorkspacePage.vue'
import { mountApp, flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import type { ContentSessionDetail, PipelineReport, SectionState, WorkspaceState } from '@/types/api'

const KEYS = ['summary', 'key_concepts', 'deep_dive', 'real_world_applications', 'viva_voice', 'practical_guide', 'learning_roadmap', 'flashcards', 'recommendations', 'agent_intro']
const TITLES: Record<string, string> = { summary: 'Summary', key_concepts: 'Key concepts', deep_dive: 'Deep dive', real_world_applications: 'Real-world applications', viva_voice: 'Interview Q&A', practical_guide: 'Practical implementation guide', learning_roadmap: 'Roadmap', flashcards: 'Flashcards', recommendations: 'Recommendations', agent_intro: 'Tutor intro' }   // backend titles: the workspace shows its own
const now = new Date().toISOString()
const profile = { career: { slug: 'devops-engineer', title: 'DevOps Engineer' }, level: 'Expert', weak: ['Safety and reliability'], strong: ['Agent fundamentals'], diagnostic_session_id: 'd1', fingerprint: 'p1' }
const detail = (missing: string[] = [], o: Partial<ContentSessionDetail> = {}): ContentSessionDetail => ({
  id: 's1', title: 'Agentic AI', description: '', topic: 'Agentic AI', content_type: 'study_guide', learning_level: 'Expert', language: 'en', status: 'active', total_sections: 10, completed_sections: 10 - missing.length,
  created_at: now, updated_at: now, last_accessed_at: null, archived_at: null, tags: [], active_jobs: [], metadata: { request: { topic: 'Agentic AI', level: 'Expert', score: 9 } }, context: { profile },
  sections: KEYS.map((k) => ({ section_key: k, section_title: TITLES[k], status: missing.includes(k) ? 'NOT_STARTED' : 'COMPLETED', content_version: missing.includes(k) ? 0 : 1, has_content: !missing.includes(k), updated_at: now })), ...o })
const contentOf = (missing: string[] = []) => ({ session_id: 's1', topic: 'Agentic AI', level: 'Expert', incomplete_sections: missing, sections: Object.fromEntries(KEYS.filter((k) => !missing.includes(k)).map((k) => [k, k === 'key_concepts' ? [{ term: 'Agents', definition: 'Autonomous loops' }, { term: 'Tools', definition: 'Callable functions' }] : k === 'flashcards' ? [{ question: 'q', answer: 'a' }] : k === 'recommendations' ? { weak_areas: ['Safety'], next_topics: [], practice_suggestions: [] } : `## ${TITLES[k]}\n\nBody of ${k}.\n\n- First useful point about ${k} here\n- Second useful point about ${k} here`])) })
const report = (rows: Partial<PipelineReport['sections'][number]>[] = [], over = {}): PipelineReport => ({ sections: KEYS.map((k) => ({ section_key: k, title: TITLES[k], status: 'COMPLETED', version: 1, source: 'generated' as const, input_tokens: 50, output_tokens: 150, latency_ms: 3000, attempts: 1, job_status: 'COMPLETED', error: null, error_code: null, ...(rows.find((r) => r.section_key === k) ?? {}) })),
  active_jobs: 0, totals: { sections: 10, completed: 10, generated: 10, reused: 0, failed: 0, input_tokens: 500, output_tokens: 1500, latency_ms: 30000 }, profile, ...over })

function sseResponse(events: [string, object][]) { return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(''))); c.close() } }), { status: 200 }) }

function setup(o: { missing?: string[]; failed?: string; route?: string; state?: Record<string, Partial<SectionState>> } = {}) {
  const missing = o.missing ?? []
  const store: Record<string, SectionState> = Object.fromEntries(Object.entries(o.state ?? {}).map(([k, v]) => [k, { understood: false, bookmarked: false, note: '', opened_at: null, ...v }]))
  const wsState = (): WorkspaceState => ({ sections: store, progress: { total: 10, understood: 0, opened: 0, bookmarked: 0, understood_pct: 0, reading_pct: 0 } })
  const spies = {
    get: vi.spyOn(api.historyApi, 'get').mockResolvedValue(detail(missing)),
    content: vi.spyOn(api.historyApi, 'content').mockResolvedValue(contentOf(missing)),
    state: vi.spyOn(api.workspaceApi, 'state').mockImplementation(async () => structuredClone(wsState())),
    pipeline: vi.spyOn(api.workspaceApi, 'pipeline').mockResolvedValue(report(o.failed ? [{ section_key: o.failed, status: 'FAILED', source: null, job_status: 'FAILED', error: 'section could not be generated', attempts: 2 }] : missing.map((k) => ({ section_key: k, status: 'NOT_STARTED', source: null, job_status: null })), {})),
    setState: vi.spyOn(api.workspaceApi, 'setState').mockImplementation(async (_id, key, patch) => {
      const cur: SectionState = store[key] ?? { understood: false, bookmarked: false, note: '', opened_at: null }
      store[key] = { understood: patch.understood ?? cur.understood, bookmarked: patch.bookmarked ?? cur.bookmarked, note: patch.note ?? cur.note, opened_at: patch.opened ? now : cur.opened_at }; return store[key] }),
    startCheck: vi.spyOn(api.workspaceApi, 'startCheck'), submitCheck: vi.spyOn(api.workspaceApi, 'submitCheck'),
  }
  const fetchSpy = vi.fn(); vi.stubGlobal('fetch', fetchSpy)
  const stubs = { MarkdownView: { props: ['source'], template: '<div data-test="md">{{ source }}</div>' }, FlashcardDeck: { template: '<div data-test="deck" />' }, KnowledgeCheck: false }
  return { spies, store, fetchSpy, mount: async () => { const m = await mountApp(StudyWorkspacePage, { route: o.route ?? '/study/learn/s1', global: { stubs } }); await flushPromises(); await new Promise((r) => setTimeout(r, 30)); await flushPromises(); return m } }
}
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().includes(t))!
const tab = (w: any, title: string) => w.findAll('[role=tab]').find((t: any) => t.text().startsWith(title))!

beforeEach(() => localStorage.setItem('learnai_token', 't'))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

describe('restoring a saved session (refresh persistence)', () => {
  it('shows topic, the learner\'s own career goal, level, weak areas and progress from stored data, and calls no generation endpoint', async () => {
    const { fetchSpy, mount } = setup({ state: { summary: { understood: true, opened_at: now }, key_concepts: { opened_at: now } } }); const { wrapper } = await mount()
    expect(wrapper.get('[data-test=topic]').text()).toBe('Agentic AI'); expect(wrapper.get('[data-test=career]').text()).toBe('DevOps Engineer'); expect(wrapper.get('[data-test=level]').text()).toBe('Expert')
    expect(wrapper.text()).toContain('Extra depth on your weak areas'); expect(wrapper.text()).toContain('Safety and reliability'); expect(wrapper.text()).toContain('Not re-teaching: Agent fundamentals')
    expect(wrapper.get('[data-test=overall-pct]').text()).toBe('10%'); expect(wrapper.get('[data-test=reading-pct]').text()).toBe('20%'); expect(wrapper.get('[data-test=completed]').text()).toBe('1 / 10')
    expect(wrapper.text()).toContain('Table of Contents'); expect(wrapper.text()).toContain('2 of 10 sections read'); expect(wrapper.findAll('[role=tab]')).toHaveLength(10)
    expect(wrapper.text()).toContain('10 of 10 sections ready'); expect(fetchSpy).not.toHaveBeenCalled()
  })
  it('contains nothing specific to any career: a different goal and topic render the same way', async () => {
    const { spies, mount } = setup(); spies.get.mockResolvedValue(detail([], { topic: 'Kubernetes', context: { profile: { ...profile, career: { slug: 'cloud-engineer', title: 'Cloud Engineer' }, weak: [], strong: [] } } }))
    const { wrapper } = await mount(); expect(wrapper.get('[data-test=career]').text()).toBe('Cloud Engineer'); expect(wrapper.text()).not.toMatch(/AI Engineer|Agentic/); expect(wrapper.text()).not.toContain('Extra depth')
    spies.get.mockResolvedValue(detail([], { context: { profile: { ...profile, career: null, weak: [], strong: [] } } }))
    const again = await (async () => { const m = await mountApp(StudyWorkspacePage, { route: '/study/learn/s1', global: { stubs: { MarkdownView: true } } }); await flushPromises(); await new Promise((r) => setTimeout(r, 30)); await flushPromises(); return m })()
    expect(again.wrapper.text()).toContain('Set a goal')
  })
  it('renders each section type with the right component, and derives objectives and takeaways from the content', async () => {
    const { mount } = setup(); const { wrapper } = await mount()
    expect(wrapper.get('[data-test=section-title]').text()).toBe('Overview'); expect(wrapper.get('[aria-label="Learning objectives"]').text()).toContain('Understand Agents'); expect(wrapper.get('[aria-label="Key takeaways"]').text()).toContain('First useful point about summary here')
    await tab(wrapper, 'Key Concepts').trigger('click'); await flushPromises(); expect(wrapper.text()).toContain('Callable functions'); expect(wrapper.text()).toContain('2 key concepts')
    await tab(wrapper, 'Flashcards').trigger('click'); await flushPromises(); expect(wrapper.find('[data-test=deck]').exists()).toBe(true)
    await tab(wrapper, 'Recommendations').trigger('click'); await flushPromises(); expect(wrapper.text()).toContain('Weak areas')
  })
  it('a deleted session is a clear message, not a broken page', async () => {
    const { spies, mount } = setup(); spies.get.mockRejectedValue(new ApiError('content session not found', 404)); const { wrapper } = await mount(); expect(wrapper.text()).toContain('This study session no longer exists')
  })
})

describe('generation: only what is missing, only when asked', () => {
  it('a missing section shows Generate; clicking asks the server for exactly that section (never the completed ones)', async () => {
    const { fetchSpy, mount } = setup({ missing: ['deep_dive'] }); fetchSpy.mockResolvedValue(sseResponse([['meta', { sections: ['deep_dive'], session_id: 's1' }], ['section', { name: 'deep_dive', value: '## Deep', ok: true }], ['done', { incomplete_sections: [] }]]))
    const { wrapper } = await mount(); expect(wrapper.text()).toContain('10 of 10 sections ready'.replace('10 of', '9 of')); expect(fetchSpy).not.toHaveBeenCalled()    // nothing starts by itself
    await tab(wrapper, 'Deep Dive').trigger('click'); await flushPromises(); expect(wrapper.text()).toContain('This section has not been generated yet')
    await btn(wrapper, 'Generate this section').trigger('click'); await flushPromises()
    const [url, init] = fetchSpy.mock.calls[0] as any; expect(url).toContain('/learning/generate-content/stream')
    expect(JSON.parse(init.body)).toEqual({ topic: 'Agentic AI', level: 'Expert', score: 9, diagnostic_session_id: 'd1', session_id: 's1', sections: ['deep_dive'] })
  })
  it('Continue generating (monitor) requests only the missing sections', async () => {
    const { fetchSpy, mount } = setup({ missing: ['deep_dive', 'flashcards'] }); fetchSpy.mockResolvedValue(sseResponse([['done', { incomplete_sections: [] }]]))
    const { wrapper } = await mount(); await btn(wrapper, 'Continue generating').trigger('click'); await flushPromises()
    expect(JSON.parse((fetchSpy.mock.calls[0] as any)[1].body).sections).toEqual(['deep_dive', 'flashcards'])
  })
  it('a failed task shows its error and time in the expanded monitor; Retry sends only that section', async () => {
    const { fetchSpy, mount } = setup({ missing: ['flashcards'], failed: 'flashcards' }); fetchSpy.mockResolvedValue(sseResponse([['done', { incomplete_sections: [] }]]))
    const { wrapper } = await mount(); expect(wrapper.text()).toContain('Needs attention')
    await wrapper.get('button[aria-label="Expand details"]').trigger('click'); const d = wrapper.get('[data-test=pipeline-details]').text()
    expect(d).toContain('Failed'); expect(d).toContain('section could not be generated'); expect(d).toContain('Completed · 3.0s · 200 tok')
    await wrapper.get('button[aria-label="Retry Flashcards"]').trigger('click'); await flushPromises()
    expect(JSON.parse((fetchSpy.mock.calls[0] as any)[1].body).sections).toEqual(['flashcards'])
  })
  it('a new topic starts one stream with the diagnostic link, then moves to the saved session URL; sections appear as they arrive', async () => {
    const { fetchSpy, mount } = setup({ route: '/study/learn?topic=Rust&level=Beginner&score=3&diag=d9' })
    fetchSpy.mockResolvedValue(sseResponse([['meta', { sections: ['summary'], session_id: 's9', stages: [{ id: 'section:summary', label: 'Write summary', status: 'pending' }] }], ['stage', { id: 'section:summary', label: 'Write summary', status: 'running' }], ['section', { name: 'summary', value: '## Rust', ok: true }], ['done', { incomplete_sections: [] }]]))
    const { wrapper, router } = await mount()
    expect(fetchSpy).toHaveBeenCalledTimes(1); expect(JSON.parse((fetchSpy.mock.calls[0] as any)[1].body)).toEqual({ topic: 'Rust', level: 'Beginner', score: 3, diagnostic_session_id: 'd9' })
    await vi.waitFor(() => expect(router.currentRoute.value.params.id).toBe('s9'), { timeout: 3000 }); expect(router.currentRoute.value.name).toBe('study-learn'); expect(wrapper.text()).toContain('Rust')
  })
})

describe('live preview while a section is being written', () => {
  const enc = new TextEncoder()
  const frame = (e: string, d: object) => enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
  function openStream() {
    let ctl!: ReadableStreamDefaultController<Uint8Array>
    const res = new Response(new ReadableStream<Uint8Array>({ start(c) { ctl = c } }), { headers: { 'Content-Type': 'text/event-stream' } })
    return { res, send: (e: string, d: object) => ctl.enqueue(frame(e, d)), close: () => ctl.close() }
  }
  const settle = async () => { await flushPromises(); await new Promise((r) => setTimeout(r, 20)); await flushPromises() }

  it('shows the text as chunks arrive (before the section is validated), then swaps in the final section; previews are not counted as content', async () => {
    const { fetchSpy, mount } = setup({ missing: ['deep_dive'] }); const s = openStream(); fetchSpy.mockResolvedValue(s.res)
    const { wrapper } = await mount()
    await tab(wrapper, 'Deep Dive').trigger('click'); await flushPromises()
    await btn(wrapper, 'Generate this section').trigger('click'); await settle()
    s.send('meta', { sections: ['deep_dive'], session_id: 's1', stages: [{ id: 'section:deep_dive', label: 'Write technical deep dive', status: 'pending' }] })
    s.send('stage', { id: 'section:deep_dive', label: 'Write technical deep dive', status: 'running' }); await settle()
    expect(wrapper.find('[data-test=md]').exists()).toBe(false)                                            // nothing written yet: skeleton, not an empty preview
    s.send('chunk', { name: 'deep_dive', attempt: 1, seq: 0, kind: 'md', delta: '## Partial heading\n' }); await settle()
    expect(wrapper.get('[data-test=md]').text()).toContain('Partial heading'); expect(wrapper.get('[data-testid=section-writing]').text()).toContain('live preview')
    expect(wrapper.text()).not.toContain('This section has not been generated yet')
    s.send('chunk', { name: 'deep_dive', attempt: 1, seq: 1, kind: 'md', delta: 'Second line\n' }); await settle()
    expect(wrapper.get('[data-test=md]').text()).toBe('## Partial heading\nSecond line')                 // deltas append in order
    s.send('chunk', { name: 'deep_dive', attempt: 1, seq: 1, kind: 'md', delta: 'DUPLICATE' }); await settle()
    expect(wrapper.get('[data-test=md]').text()).not.toContain('DUPLICATE')                                  // a replayed seq is ignored
    expect(wrapper.text()).not.toContain('10 of 10 sections ready'); expect(wrapper.find('[aria-label="Knowledge check"]').exists()).toBe(false)   // a preview is not a finished section
    s.send('section', { name: 'deep_dive', value: '## Final validated text', ok: true }); s.send('done', { incomplete_sections: [] }); s.close(); await settle()
    expect(wrapper.get('[data-test=md]').text()).toBe('## Final validated text'); expect(wrapper.find('[data-testid=section-writing]').exists()).toBe(false)
  })

  it('a section that fails discards its half-written preview instead of showing it as if it were finished', async () => {
    const { fetchSpy, mount } = setup({ missing: ['deep_dive'] }); const s = openStream(); fetchSpy.mockResolvedValue(s.res)
    const { wrapper } = await mount()
    await tab(wrapper, 'Deep Dive').trigger('click'); await flushPromises()
    await btn(wrapper, 'Generate this section').trigger('click'); await settle()
    s.send('meta', { sections: ['deep_dive'], session_id: 's1' }); s.send('stage', { id: 'section:deep_dive', label: 'x', status: 'running' })
    s.send('chunk', { name: 'deep_dive', attempt: 1, seq: 0, kind: 'md', delta: 'Half written…\n' }); await settle()
    expect(wrapper.get('[data-test=md]').text()).toContain('Half written')
    s.send('section', { name: 'deep_dive', value: '', ok: false }); s.send('done', { incomplete_sections: ['deep_dive'] }); s.close(); await settle()
    expect(wrapper.text()).not.toContain('Half written')
  })
})

describe('reading state is saved, not just displayed', () => {
  it('opening a section records it as read once; bookmark and understood persist and update progress', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount()
    expect(spies.setState).toHaveBeenCalledWith('s1', 'summary', { opened: true }); expect(spies.setState.mock.calls.filter((c) => c[2].opened && c[1] === 'summary')).toHaveLength(1)
    await btn(wrapper, 'Bookmark').trigger('click'); await flushPromises(); expect(spies.setState).toHaveBeenCalledWith('s1', 'summary', { bookmarked: true }); expect(wrapper.text()).toContain('Bookmarked')
    await btn(wrapper, 'Mark as Understood').trigger('click'); await flushPromises(); expect(spies.setState).toHaveBeenCalledWith('s1', 'summary', { understood: true })
    expect(wrapper.text()).toContain('Understood'); expect(wrapper.get('[data-test=completed]').text()).toBe('1 / 10')
    await btn(wrapper, 'Understood').trigger('click'); await flushPromises(); expect(spies.setState).toHaveBeenCalledWith('s1', 'summary', { understood: false }); expect(wrapper.get('[data-test=completed]').text()).toBe('0 / 10')
  })
  it('a failed save is rolled back and reported (nothing is silently lost)', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount(); spies.setState.mockRejectedValueOnce(new ApiError('save failed', 500))
    await btn(wrapper, 'Mark as Understood').trigger('click'); await flushPromises(); await new Promise((r) => setTimeout(r, 20)); await flushPromises()
    expect(wrapper.get('[data-test=completed]').text()).toBe('0 / 10'); expect(wrapper.text()).toContain('Mark as Understood')
  })
  it('notes autosave (debounced) to the session and come back after a refresh', async () => {
    const { spies, mount } = setup({ state: { summary: { note: 'remember decorators', opened_at: now } } }); const { wrapper } = await mount()
    const box = wrapper.get('textarea'); expect((box.element as HTMLTextAreaElement).value).toBe('remember decorators')
    await box.setValue('remember decorators wrap'); expect(spies.setState).not.toHaveBeenCalledWith('s1', 'summary', { note: expect.anything() }); await new Promise((r) => setTimeout(r, 800)); await flushPromises()
    expect(spies.setState).toHaveBeenCalledWith('s1', 'summary', { note: 'remember decorators wrap' }); expect(wrapper.text()).toContain('Saved')
  })
})

describe('navigation, knowledge check and tutor', () => {
  it('previous / next, Alt+arrows and the table of contents move between sections and keep the URL in sync', async () => {
    const { mount } = setup(); const { wrapper, router } = await mount()
    expect((btn(wrapper, 'Overview') ? 1 : 0)).toBe(1); expect(wrapper.findAll('button').find((b) => b.text() === 'Key Concepts' && b.attributes('aria-current') === undefined)).toBeTruthy()
    const next = wrapper.findAll('button').filter((b) => b.text().includes('Key Concepts') && !b.attributes('role') && !b.attributes('aria-current')).at(-1)!; await next.trigger('click'); await flushPromises()
    expect(wrapper.get('[data-test=section-title]').text()).toBe('Key Concepts'); await vi.waitFor(() => expect(router.currentRoute.value.query.s).toBe('key_concepts'))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', altKey: true })); await flushPromises(); expect(wrapper.get('[data-test=section-title]').text()).toBe('Deep Dive')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', altKey: true })); await flushPromises(); expect(wrapper.get('[data-test=section-title]').text()).toBe('Key Concepts')
    const arrow = tab(wrapper, 'Key Concepts'); await arrow.trigger('keydown', { key: 'ArrowRight' }); await flushPromises(); expect(wrapper.get('[data-test=section-title]').text()).toBe('Deep Dive')
  })
  it('knowledge check: loads saved questions on request, grades on the server, shows explanations, offers another set', async () => {
    const { spies, mount } = setup()
    spies.startCheck.mockResolvedValue({ available: true, check_id: 'c1', questions: [0, 1].map((i) => ({ question_id: `q${i}`, type: 'multiple_choice' as const, prompt: `Q${i}?`, options: ['A', 'B'], difficulty: 2, objective: 'x' })) })
    spies.submitCheck.mockResolvedValue({ check_id: 'c1', score: 1, total: 2, passed: false, results: [{ question_id: 'q0', correct: true, selected: 0, correct_options: [0], explanation: 'Because A.' }, { question_id: 'q1', correct: false, selected: 0, correct_options: [1], explanation: 'Because B.' }] })
    const { wrapper } = await mount(); expect(spies.startCheck).not.toHaveBeenCalled()
    await btn(wrapper, 'Test your understanding').trigger('click'); await flushPromises(); expect(spies.startCheck).toHaveBeenCalledWith('s1', 'summary', { round: 0, n: 3 }); expect(wrapper.text()).toContain('Q0?')
    expect(btn(wrapper, 'Check answers').attributes('disabled')).toBeDefined()
    for (const r of wrapper.findAll('input[type=radio]').filter((_, i) => i % 2 === 0)) await r.setValue(true)
    await btn(wrapper, 'Check answers').trigger('click'); await flushPromises(); expect(spies.submitCheck).toHaveBeenCalledWith('c1', { q0: 0, q1: 0 })
    expect(wrapper.text()).toContain('Because A.'); expect(wrapper.text()).toContain('Because B.'); expect(wrapper.text()).toContain('1 / 2'); expect(wrapper.text()).toContain('Worth another look')
    await btn(wrapper, 'Try another set').trigger('click'); await flushPromises(); expect(spies.startCheck).toHaveBeenLastCalledWith('s1', 'summary', { round: 1, n: 3 })
  })
  it('a topic with no saved questions says so instead of generating anything', async () => {
    const { spies, fetchSpy, mount } = setup(); spies.startCheck.mockResolvedValue({ available: false, check_id: null, questions: [], message: 'There are no saved questions for this topic yet, so there is no knowledge check to take.' })
    const { wrapper } = await mount(); await btn(wrapper, 'Test your understanding').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('no saved questions for this topic'); expect(fetchSpy).not.toHaveBeenCalled()
  })
  it('Ask AI Tutor opens the tutor with this section\'s question prefilled for the learner to send', async () => {
    const { mount } = setup(); const { wrapper, router } = await mount(); await btn(wrapper, 'Chat with AI Tutor').trigger('click'); await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('tutor'), { timeout: 3000 }); expect(router.currentRoute.value.query.skill).toBe('Agentic AI'); expect(String(router.currentRoute.value.query.ask)).toContain('Overview')
  })
  it('recommends the next unfinished section and the layout is responsive (sticky scrollable nav, sidebar only beside the content on large screens)', async () => {
    const { mount } = setup({ state: { summary: { understood: true, opened_at: now } } }); const { wrapper } = await mount()
    expect(wrapper.get('aside').text()).toContain('Recommended Next'); expect(wrapper.get('aside').text()).toContain('Key Concepts')
    const nav = wrapper.get('nav[aria-label="Study sections"]'); expect(nav.classes()).toEqual(expect.arrayContaining(['sticky', 'top-14'])); expect(nav.get('[role=tablist]').classes()).toContain('overflow-x-auto')
    expect(wrapper.html()).toMatch(/lg:grid-cols-\[minmax\(0,1fr\)_17rem\]/)
  })
})
