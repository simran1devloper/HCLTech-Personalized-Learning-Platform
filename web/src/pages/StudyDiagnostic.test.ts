import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StudyPage from './StudyPage.vue'
import { mountApp, flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import { normalizeTopic, stepsFor, topicError } from '@/features/study/diagnostic'
import type { DiagnosticPlan, DiagnosticSession, DiagnosticSessionSummary } from '@/types/api'

const plan = (o: Partial<DiagnosticPlan> = {}): DiagnosticPlan => ({ topic: 'Python', canonical_topic: 'Python', known: true, plan: 'question_bank', eligible: 18, requested: 10, missing: 0, will_call_ai: false, ...o })
const session = (o: Partial<DiagnosticSession> = {}): DiagnosticSession => ({ session_id: 's1', topic: 'Python', question_count: 3, generation_source: 'question_bank', status: 'ready', answers: {},
  questions: [0, 1, 2].map((i) => ({ question_id: `q${i}`, type: 'multiple_choice' as const, prompt: `Question ${i + 1}?`, options: ['A', 'B', 'C'], difficulty: 2, objective: 'Types' })), ...o })
const done = (o = {}) => session({ status: 'completed', result: { topic: 'Python', score: 2, total: 3, score_pct: 66.7, score_10: 7, level: 'Intermediate',
  breakdown: [{ question: 'Question 1?', user_answer: 'A', correct_answer: 'B', correct: false, explanation: 'Because B.' }] }, ...o })
const none = { items: [], total: 0 }

function setup({ check = plan(), start = session() as DiagnosticSession | Promise<DiagnosticSession>, sessions = none as any, route = '/study' } = {}) {
  const spies = {
    suggest: vi.spyOn(api.diagnosticApi, 'suggest').mockResolvedValue({ items: [{ skill_id: 'py', name: 'Python', category: 'programming', question_count: 18 }] }),
    check: vi.spyOn(api.diagnosticApi, 'check').mockResolvedValue(check),
    start: vi.spyOn(api.diagnosticApi, 'start').mockImplementation(() => Promise.resolve(start) as any),
    sessions: vi.spyOn(api.diagnosticApi, 'sessions').mockResolvedValue(sessions),
    session: vi.spyOn(api.diagnosticApi, 'session').mockResolvedValue(session()),
    submit: vi.spyOn(api.diagnosticApi, 'submit').mockResolvedValue(done()),
    save: vi.spyOn(api.assessmentApi, 'saveAnswers').mockResolvedValue({ saved: 1, answered_questions: 1, total_questions: 3 }),
    legacy: vi.spyOn(api.legacyApi, 'history').mockResolvedValue({ history: [] }),
  }
  return { spies, mount: async () => { const m = await mountApp(StudyPage, { route }); await flushPromises(); return m } }
}
const btn = (w: any, t: string) => w.findAll('button').find((b: any) => b.text().includes(t))!
const type = async (w: any, v: string) => { await w.get('input[aria-label=Topic]').setValue(v); await flushPromises() }

beforeEach(() => localStorage.setItem('learnai_token', 't'))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

describe('progress steps mirror the backend plan', () => {
  it('never mention AI when the plan is database-only; mention it only when generation will really happen', () => {
    const labels = (p: any) => stepsFor(p).map((s) => s.label).join(' | ')
    expect(labels(null)).toBe('Checking question bank…')
    const bank = labels(plan()); expect(bank).toContain('Checking question bank…'); expect(bank).toContain('Preparing your diagnostic quiz…'); expect(bank).not.toMatch(/generat|AI|not found/i)
    const fresh = labels(plan({ known: false, plan: 'ai_generated', eligible: 0, missing: 10, will_call_ai: true })); expect(fresh).toContain('Topic not found in question bank.'); expect(fresh).toContain('Generating and validating questions…'); expect(fresh).toContain('Preparing your quiz…')
    const part = labels(plan({ plan: 'hybrid', eligible: 6, missing: 4, will_call_ai: true })); expect(part).toContain('Found 6 saved questions; 4 more needed.'); expect(part).toContain('Generating and validating 4 more…'); expect(part).not.toContain('not found')
  })
  it('normalises and validates topics locally', () => {
    expect(normalizeTopic('  python   programming ')).toBe('python programming')
    expect(topicError('')).toMatch(/at least two/); expect(topicError('x')).toMatch(/at least two/); expect(topicError('a'.repeat(81))).toMatch(/80/); expect(topicError('<script>')).toMatch(/letters/)
    for (const ok of ['Python', 'C++', 'C#', 'Node.js', 'CI/CD', 'REST API design', 'Data Structures & Algorithms']) expect(topicError(ok)).toBeNull()
  })
})

describe('Generate quiz', () => {
  it('is disabled for empty or invalid input and enabled for a real topic', async () => {
    const { mount } = setup(); const { wrapper } = await mount()
    expect(btn(wrapper, 'Generate quiz').attributes('disabled')).toBeDefined()
    await type(wrapper, 'x'); expect(btn(wrapper, 'Generate quiz').attributes('disabled')).toBeDefined(); expect(wrapper.text()).toContain('at least two characters')
    await type(wrapper, '<b>'); expect(wrapper.text()).toContain('letters, numbers and spaces')
    await type(wrapper, 'Python'); expect(btn(wrapper, 'Generate quiz').attributes('disabled')).toBeUndefined()
  })
  it('checks the bank, starts a session with the normalised topic and an idempotency key, then shows the questions without any answers', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount()
    await type(wrapper, '  Python   '); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(spies.check).toHaveBeenCalledWith({ topic: 'Python', question_count: 10 }); expect(spies.start.mock.calls[0][0]).toEqual({ topic: 'Python', question_count: 10 })
    expect(spies.start.mock.calls[0][1]).toMatch(/^[0-9a-f-]{36}$/)
    expect(wrapper.text()).toContain('Question 1?'); expect(wrapper.text()).toContain('Question bank'); expect(wrapper.findAll('input[type=radio]')).toHaveLength(9)
    expect(wrapper.text()).not.toMatch(/correct answer|explanation/i)
  })
  it('database path: shows only bank wording while starting (no AI messages)', async () => {
    let release!: (s: DiagnosticSession) => void
    const { mount } = setup({ start: new Promise<DiagnosticSession>((r) => (release = r)) }); const { wrapper } = await mount()
    await type(wrapper, 'Python'); await wrapper.get('form').trigger('submit'); await flushPromises()
    const t = wrapper.get('[aria-label="Quiz preparation progress"]').text()
    expect(t).toContain('Checking question bank…'); expect(t).toContain('Found this topic in the question bank.'); expect(t).toContain('Preparing your diagnostic quiz…'); expect(t).not.toMatch(/generat|AI/i)
    release(session()); await flushPromises()
  })
  it('AI-fallback path: says the topic was not found and that questions are being generated', async () => {
    let release!: (s: DiagnosticSession) => void
    const { mount } = setup({ check: plan({ topic: 'Rust Programming', canonical_topic: null, known: false, plan: 'ai_generated', eligible: 0, missing: 10, will_call_ai: true }), start: new Promise<DiagnosticSession>((r) => (release = r)) })
    const { wrapper } = await mount(); await type(wrapper, 'Rust Programming'); await wrapper.get('form').trigger('submit'); await flushPromises()
    const t = wrapper.get('[aria-label="Quiz preparation progress"]').text(); expect(t).toContain('Topic not found in question bank.'); expect(t).toContain('Generating and validating questions…')   // shown immediately: that is what the request is doing
    release(session({ topic: 'Rust Programming', generation_source: 'ai_generated' })); await flushPromises(); expect(wrapper.text()).toContain('Newly generated')
  })
  it('disables the button and ignores repeated submits while starting (one request)', async () => {
    let release!: (s: DiagnosticSession) => void
    const { spies, mount } = setup({ start: new Promise<DiagnosticSession>((r) => (release = r)) }); const { wrapper } = await mount()
    await type(wrapper, 'Python'); const form = wrapper.get('form')
    await form.trigger('submit'); await form.trigger('submit'); await form.trigger('submit'); await flushPromises()
    expect(spies.start).toHaveBeenCalledTimes(1); expect(btn(wrapper, 'Generate quiz').attributes('disabled')).toBeDefined(); expect(btn(wrapper, 'Generate quiz').attributes('aria-busy')).toBe('true')
    release(session()); await flushPromises()
  })
  it('shows a clear inline error with Try again; a retry uses a new key and succeeds', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount()
    spies.start.mockRejectedValueOnce(new ApiError('Generating questions for Nim took too long. Nothing was saved. Please try again in a moment.', 503, 'questions_unavailable'))
    await type(wrapper, 'Nim'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('took too long'); expect(wrapper.text()).toContain('Generate quiz')
    await btn(wrapper.get('[role=alert]') as any, 'Try again').trigger('click'); await flushPromises()
    expect(spies.start).toHaveBeenCalledTimes(2); expect(spies.start.mock.calls[1][1]).not.toBe(spies.start.mock.calls[0][1]); expect(wrapper.text()).toContain('Question 1?')
  })
  it('reports a validation error from the server and a network failure in plain language', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount()
    spies.check.mockRejectedValueOnce(new ApiError('Enter a topic of at least two characters.', 422, 'validation_failed'))
    await type(wrapper, 'Python'); await wrapper.get('form').trigger('submit'); await flushPromises(); expect(wrapper.get('[role=alert]').text()).toContain('at least two characters'); expect(spies.start).not.toHaveBeenCalled()
    spies.start.mockRejectedValueOnce(new ApiError('Unable to reach the server.', 0, 'network')); await btn(wrapper.get('[role=alert]') as any, 'Try again').trigger('click'); await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('Could not reach the server')
  })
  it('offers registry suggestions through a datalist but accepts any free text', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount(); await type(wrapper, 'pyth'); await new Promise((r) => setTimeout(r, 260)); await flushPromises()
    expect(spies.suggest).toHaveBeenCalledWith('pyth', expect.anything()); expect(wrapper.findAll('#topic-suggestions option').map((o) => o.attributes('value'))).toEqual(['Python'])
    await type(wrapper, 'Something Totally New'); expect(btn(wrapper, 'Generate quiz').attributes('disabled')).toBeUndefined()
  })
})

describe('quiz, results and study material', () => {
  it('autosaves answers to the server, submits them for server-side grading and shows the graded result', async () => {
    const { spies, mount } = setup(); const { wrapper } = await mount()
    await type(wrapper, 'Python'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(btn(wrapper, 'Submit quiz').attributes('disabled')).toBeDefined()
    const radios = wrapper.findAll('input[type=radio]'); for (const i of [0, 3, 6]) await radios[i].setValue(true)
    await new Promise((r) => setTimeout(r, 650)); await flushPromises()
    expect(spies.save).toHaveBeenCalled(); expect(wrapper.text()).toContain('Answers saved')
    const saved = Object.assign({}, ...spies.save.mock.calls.map((c) => c[1])); expect(saved).toEqual({ q0: 0, q1: 0, q2: 0 })
    await btn(wrapper, 'Submit quiz (3/3)').trigger('click'); await flushPromises()
    expect(spies.submit).toHaveBeenCalledWith('s1', { q0: 0, q1: 0, q2: 0 })
    expect(wrapper.text()).toContain('Result (graded by the server)'); expect(wrapper.text()).toContain('2 / 3'); expect(wrapper.text()).toContain('Intermediate'); expect(wrapper.text()).toContain('Correct: B'); expect(wrapper.text()).toContain('Because B.')
  })
  it('Generate study material opens the learning workspace with the topic, level, 0-10 score and the diagnostic session that personalises it', async () => {
    const { spies, mount } = setup({ route: '/study?session=s1' }); spies.session.mockResolvedValue(done())
    const { wrapper, router } = await mount(); await flushPromises()
    expect(wrapper.text()).toContain('Result (graded by the server)')
    await btn(wrapper, 'Generate study material').trigger('click'); await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('study-learn'), { timeout: 5000 })
    expect(router.currentRoute.value.query).toMatchObject({ topic: 'Python', level: 'Intermediate', score: '7', diag: 's1' })
  })
})

describe('previous sessions', () => {
  const rows: DiagnosticSessionSummary[] = [
    { session_id: 'open1', topic: 'Redis', status: 'open', question_count: 10, answered_questions: 4, score_pct: null, level: null, generation_source: 'question_bank', started_at: new Date().toISOString(), completed_at: null },
    { session_id: 'done1', topic: 'Docker', status: 'completed', question_count: 10, answered_questions: 10, score_pct: 80, level: 'Expert', generation_source: 'hybrid', started_at: new Date().toISOString(), completed_at: new Date().toISOString() },
  ]
  it('lists sessions with status, source, progress and Resume / Review', async () => {
    const { mount } = setup({ sessions: { items: rows, total: 2 } }); const { wrapper } = await mount()
    const t = wrapper.text(); expect(t).toContain('Previous sessions'); expect(t).toContain('Redis'); expect(t).toContain('In progress'); expect(t).toContain('4/10 answered'); expect(t).toContain('Question bank')
    expect(t).toContain('Docker'); expect(t).toContain('Completed'); expect(t).toContain('8/10 · Expert'); expect(t).toContain('Bank + generated')
    const li = wrapper.findAll('li').filter((l) => l.text().includes('Redis')); expect(btn(li[0], 'Resume')).toBeTruthy()
    expect(btn(wrapper.findAll('li').filter((l) => l.text().includes('Docker'))[0], 'Review')).toBeTruthy()
  })
  it('Resume reopens the saved quiz with its saved answers and does not start a new session', async () => {
    const { spies, mount } = setup({ sessions: { items: rows, total: 2 } }); const { wrapper } = await mount()
    spies.session.mockResolvedValue(session({ session_id: 'open1', topic: 'Redis', answers: { q0: 2 } }))
    await btn(wrapper.findAll('li').filter((l) => l.text().includes('Redis'))[0] as any, 'Resume').trigger('click'); await flushPromises()
    expect(spies.session).toHaveBeenCalledWith('open1'); expect(spies.start).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('Question 1?')
    expect((wrapper.findAll('input[type=radio]')[2].element as HTMLInputElement).checked).toBe(true); 
  })
  it('Review opens the completed result without generating anything', async () => {
    const { spies, mount } = setup({ sessions: { items: rows, total: 2 } }); const { wrapper } = await mount()
    spies.session.mockResolvedValue(done({ session_id: 'done1', topic: 'Docker' }))
    await btn(wrapper.findAll('li').filter((l) => l.text().includes('Docker'))[0] as any, 'Review').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('Result (graded by the server)'); expect(spies.start).not.toHaveBeenCalled(); expect(spies.check).not.toHaveBeenCalled()
  })
  it('a page refresh with ?session= restores the quiz instead of creating a new one', async () => {
    const { spies, mount } = setup({ route: '/study?session=open1' }); spies.session.mockResolvedValue(session({ session_id: 'open1', answers: { q1: 1 } }))
    const { wrapper } = await mount(); await flushPromises()
    expect(spies.session).toHaveBeenCalledWith('open1'); expect(spies.start).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('Question 2?')
    expect((wrapper.findAll('input[type=radio]')[4].element as HTMLInputElement).checked).toBe(true)
  })
  it('shows an error (not a blank page) if a saved session cannot be opened', async () => {
    const { spies, mount } = setup({ route: '/study?session=gone' }); spies.session.mockRejectedValue(new ApiError('session not found', 404))
    const { wrapper } = await mount(); await flushPromises(); expect(wrapper.get('[role=alert]').text()).toContain('session not found'); expect(wrapper.text()).toContain('Generate quiz')
  })
})

describe('opened from the Skill Explorer (Start Learning)', () => {
  it('prefills the topic, shows the skill/career context with a way back, and never auto-starts the quiz', async () => {
    const { spies, mount } = setup({ route: '/study?skill=sk-1&topic=RAG&career=ai-engineer&intent=learn' })
    const { wrapper } = await mount()
    expect((wrapper.get('input[aria-label=Topic]').element as HTMLInputElement).value).toBe('RAG')
    const ctx = wrapper.get('[data-test=explorer-context]')
    expect(ctx.text()).toContain('Learning a skill from the Skill Explorer'); expect(ctx.text()).toContain('ai engineer')
    expect(ctx.get('a').attributes('href')).toContain('/skills?skill=sk-1&role=ai-engineer')
    expect(spies.start).not.toHaveBeenCalled(); expect(spies.check).not.toHaveBeenCalled()
  })
  it('says "Revising" for the revise intent, and shows nothing for a plain visit', async () => {
    const { wrapper } = await setup({ route: '/study?skill=sk-1&topic=RAG&intent=revise' }).mount()
    expect(wrapper.get('[data-test=explorer-context]').text()).toContain('Revising')
    const plain = await setup().mount(); expect(plain.wrapper.find('[data-test=explorer-context]').exists()).toBe(false)
  })
  it('starting the prefilled topic uses the normal diagnostic flow', async () => {
    const { spies, mount } = setup({ route: '/study?skill=sk-1&topic=Python&career=ai-engineer' })
    const { wrapper } = await mount()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(spies.check).toHaveBeenCalledWith({ topic: 'Python', question_count: 10 }); expect(spies.start.mock.calls[0][0]).toEqual({ topic: 'Python', question_count: 10 })
  })
})
