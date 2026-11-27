import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssessmentsPage from './AssessmentsPage.vue'
import { mountApp, flushPromises } from '@/test-utils'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import * as sse from '@/services/sse'
import type { Assessment, BankSkill, GenerationState } from '@/types/api'

const skill = (o: Partial<BankSkill> & { name: string }): BankSkill => ({ skill_id: o.name.toLowerCase().replace(/\W+/g, '-'), category: 'programming', question_count: 18, concepts: 5, proficiency: null, assessed: false,
  est_seconds_per_question: 60, recommended: false, ...o })
const SKILLS = [skill({ name: 'Python', recommended: true }), skill({ name: 'SQL', question_count: 12, concepts: 4, proficiency: 3.2, assessed: true }), skill({ name: 'JavaScript', question_count: 0, concepts: 0 }), skill({ name: 'PostgreSQL', question_count: 12 })]
const attempt = (o: Partial<Assessment> = {}): Assessment => ({ assessment_id: 'att-1', skill_id: 'python', skill: 'Python', total_questions: 10, requested: 10, shortfall: 0, estimated_minutes: 10,
  questions: Array.from({ length: 10 }, (_, i) => ({ question_id: `q${i}`, type: 'multiple_choice' as const, prompt: `Question ${i}?`, options: ['a', 'b', 'c'], difficulty: 2, objective: 'Types' })), ...o })
const list = { items: [], total: 0, offset: 0, limit: 10 }

async function setup(create = vi.fn().mockResolvedValue(attempt())) {
  vi.spyOn(api.questionBankApi, 'skills').mockResolvedValue({ items: SKILLS, total: SKILLS.length })
  vi.spyOn(api.assessmentApi, 'list').mockResolvedValue(list)
  vi.spyOn(api.assessmentApi, 'activeGeneration').mockResolvedValue({ active: null })
  vi.spyOn(api.assessmentApi, 'create').mockImplementation(create as any)
  const m = await mountApp(AssessmentsPage, { route: '/assessments' }); await flushPromises()
  return { ...m, create }
}
const startBtn = (w: any) => w.findAll('button').find((b: any) => b.text() === 'Start')!

beforeEach(() => localStorage.setItem('learnai_token', 't'))
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('Start button workflow', () => {
  it('sends the selected skill and question count with an idempotency key, then opens the quiz', async () => {
    const { wrapper, router, create } = await setup()
    expect((wrapper.get('select[class*="w-64"]').element as HTMLSelectElement).value).toBe('python')           // sensible default
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
    expect(create.mock.calls[0][0]).toEqual({ skill: 'Python', n_questions: 10 })
    expect(create.mock.calls[0][1]).toMatch(/^[0-9a-f-]{36}$/)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('assessment-run'), { timeout: 8000 }); expect(router.currentRoute.value.params.id).toBe('att-1')
  })
  it('uses the chosen skill and count, and primes the quiz cache so questions render instantly', async () => {
    const { wrapper, router, create, queryClient } = await setup(vi.fn().mockResolvedValue(attempt({ assessment_id: 'att-9', skill: 'SQL', skill_id: 'sql' })))
    const prime = vi.spyOn(queryClient, 'setQueryData')
    await wrapper.get('select[class*="w-64"]').setValue('sql'); await wrapper.get('select[class*="w-24"]').setValue('5')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(create.mock.calls[0][0]).toEqual({ skill: 'SQL', n_questions: 5 })
    await vi.waitFor(() => expect(prime).toHaveBeenCalledWith(['assessment', 'att-9'], expect.anything()))
    const cached = prime.mock.calls.find((c) => (c[0] as string[])[1] === 'att-9')![1] as any
    expect(cached.questions).toHaveLength(10); expect(cached.status).toBe('open'); await vi.waitFor(() => expect(router.currentRoute.value.params.id).toBe('att-9'), { timeout: 8000 })
  })
  it('shows a loading state and ignores a second click while the first request is in flight', async () => {
    let release!: (v: Assessment) => void
    const create = vi.fn(() => new Promise<Assessment>((r) => (release = r)))
    const { wrapper, router } = await setup(create)
    const form = wrapper.get('form')
    await form.trigger('submit'); await form.trigger('submit'); await form.trigger('submit'); await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
    expect(startBtn(wrapper).attributes('disabled')).toBeDefined(); expect(startBtn(wrapper).attributes('aria-busy')).toBe('true')
    release(attempt()); await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('assessment-run'), { timeout: 8000 })
  })
  it('never fails silently: an inline alert explains the problem and Try again recovers with a fresh idempotency key', async () => {
    const create = vi.fn().mockRejectedValueOnce(new ApiError('There are no assessment questions for Kafka yet. Please try again in a moment.', 503, 'questions_unavailable')).mockResolvedValue(attempt())
    const { wrapper, router } = await setup(create)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    const alert = wrapper.get('[role=alert]'); expect(alert.text()).toContain('no assessment questions for Kafka'); expect(router.currentRoute.value.name).toBe('assessments')
    expect(startBtn(wrapper).attributes('disabled')).toBeUndefined()                                           // the form is usable again
    await alert.findAll('button').find((b) => b.text() === 'Try again')!.trigger('click'); await flushPromises()
    expect(create).toHaveBeenCalledTimes(2); expect(create.mock.calls[1][1]).not.toBe(create.mock.calls[0][1])
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('assessment-run'), { timeout: 8000 })
  })
  it('explains a network failure in plain language', async () => {
    const { wrapper } = await setup(vi.fn().mockRejectedValue(new ApiError('Unable to reach the server. Check your connection.', 0, 'network')))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('Could not reach the server')
  })
})

describe('Assessment creation form', () => {
  it('offers recommended skills, a search, the bank availability, previous proficiency and an estimated duration', async () => {
    const { wrapper } = await setup()
    expect(wrapper.text()).toContain('Recommended'); expect(wrapper.text()).toContain('Ready: 18 validated questions across 5 concept areas'); expect(wrapper.text()).toContain('Not assessed yet')
    expect(wrapper.text()).toContain('About 10 min')
    await wrapper.get('select[class*="w-24"]').setValue('5'); expect(wrapper.text()).toContain('About 5 min')
    await wrapper.get('select[class*="w-64"]').setValue('sql'); expect(wrapper.text()).toContain('Your level: 3.2 / 5')
  })
  it('warns when a skill has no saved questions and when fewer than requested are ready', async () => {
    const { wrapper } = await setup()
    await wrapper.get('select[class*="w-64"]').setValue('javascript'); expect(wrapper.text()).toContain('No saved questions yet')
    await wrapper.get('select[class*="w-64"]').setValue('sql'); await wrapper.get('select[class*="w-24"]').setValue('15')
    expect(wrapper.text()).toContain('12 questions are ready now')
  })
  it('filters the skill list from the search box and keeps a valid selection', async () => {
    const { wrapper } = await setup()
    await wrapper.get('input[type=search]').setValue('postg'); await new Promise((r) => setTimeout(r, 260)); await flushPromises()
    const opts = wrapper.get('select[class*="w-64"]').findAll('option'); expect(opts.map((o) => o.text())).toEqual(['PostgreSQL'])
    expect((wrapper.get('select[class*="w-64"]').element as HTMLSelectElement).value).toBe('postgresql')
    await wrapper.get('input[type=search]').setValue('zzz'); await new Promise((r) => setTimeout(r, 260)); await flushPromises()
    expect(wrapper.text()).toContain('No skills match')
  })
  it('a recommended chip selects that skill; ?skill= preselects one', async () => {
    const { wrapper } = await setup()
    await wrapper.findAll('button[aria-pressed]').find((b) => b.text() === 'Python')!.trigger('click')
    expect((wrapper.get('select[class*="w-64"]').element as HTMLSelectElement).value).toBe('python')
    vi.restoreAllMocks()
    vi.spyOn(api.questionBankApi, 'skills').mockResolvedValue({ items: SKILLS, total: 4 }); vi.spyOn(api.assessmentApi, 'list').mockResolvedValue(list)
    const m = await mountApp(AssessmentsPage, { route: '/assessments?skill=SQL' }); await flushPromises()
    expect((m.wrapper.get('select[class*="w-64"]').element as HTMLSelectElement).value).toBe('sql')
  })
  it('shows an error state with retry when the skill list cannot load', async () => {
    vi.spyOn(api.questionBankApi, 'skills').mockRejectedValue(new ApiError('boom', 500)); vi.spyOn(api.assessmentApi, 'list').mockResolvedValue(list)
    const { wrapper } = await mountApp(AssessmentsPage, { route: '/assessments' }); await flushPromises()
    expect(wrapper.text()).toContain('boom'); expect(wrapper.text()).toContain('Try again')
  })
})

describe('Live generation (SSE)', () => {
  const gstate = (o: Partial<GenerationState> = {}): GenerationState => ({ assessment_id: 'gen-1', status: 'generating', requested: 10, completed: 1, progress: 10, last_seq: 3, error: null, questions: [{ question_id: 'q0', index: 0, concept: 'Types', difficulty: 1, question_type: 'single_choice', prompt: 'p', source: 'bank' }], ...o })
  const generating = (o: Partial<Assessment> = {}): Assessment => attempt({ assessment_id: 'gen-1', status: 'generating', total_questions: 1, shortfall: 9, questions: [], generation: gstate(), ...o })
  const ev = (seq: number, type: string, o: Record<string, unknown> = {}) => ({ event: type, data: { assessment_id: 'gen-1', type, seq, status: 'generating', completed: 1, requested: 10, progress: 10, timestamp: 't', ...o } })
  const savedEv = (seq: number, done: number) => ev(seq, 'question_saved', { completed: done, progress: done * 10, question: { question_id: `q${done}`, index: done - 1, concept: 'Types', difficulty: 2, source: 'generated' } })

  it('a 202 keeps the learner on the page and shows live counter, progress bar and question slots driven by events', async () => {
    let push!: (e: any) => void
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise(() => {}) })
    const { wrapper, router } = await setup(vi.fn().mockResolvedValue(generating()))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(router.currentRoute.value.name).toBe('assessments')
    expect(wrapper.get('[data-testid=generation-count]').text()).toBe('1 of 10')
    push(savedEv(4, 2)); await flushPromises()
    expect(wrapper.get('[data-testid=generation-count]').text()).toBe('2 of 10')
    expect(wrapper.get('[role=progressbar][aria-label="Questions saved"]').attributes('aria-valuenow')).toBe('20')
    expect(wrapper.get('[role=progressbar][aria-label="Questions saved"] > div').attributes('style')).toContain('width: 20%')
    expect(wrapper.findAll('li[data-state=saved]')).toHaveLength(2); expect(wrapper.findAll('li[data-state=working]')).toHaveLength(1); expect(wrapper.findAll('li[data-state=waiting]')).toHaveLength(7)
    push(ev(5, 'generation_activity', { stage: 'generating', streamed: 3, completed: 2, progress: 20 })); await flushPromises()
    expect(wrapper.get('[data-testid=generation-stage]').text()).toContain('not counted until saved'); expect(wrapper.get('[data-testid=generation-count]').text()).toBe('2 of 10')
  })

  it('Start is disabled and a second submit is ignored while a generation is running', async () => {
    vi.spyOn(sse, 'getSse').mockImplementation(() => new Promise(() => {}))
    const { wrapper, create } = await setup(vi.fn().mockResolvedValue(generating()))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(startBtn(wrapper).attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('completed opens the quiz; it never navigates before the completed event', async () => {
    let push!: (e: any) => void; let finish!: () => void
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise<void>((r) => (finish = r)) })
    vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(gstate({ status: 'completed', completed: 10, progress: 100, last_seq: 20 }))
    vi.spyOn(api.assessmentApi, 'get').mockResolvedValue({ assessment_id: 'gen-1', skill_id: 'python', skill: 'Python', status: 'open', started_at: 't', questions: attempt().questions, total_questions: 10 } as any)
    const { wrapper, router } = await setup(vi.fn().mockResolvedValue(generating()))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    push(savedEv(4, 9)); await flushPromises(); expect(router.currentRoute.value.name).toBe('assessments')
    push(ev(5, 'completed', { status: 'completed', completed: 10, progress: 100 })); finish()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('assessment-run'), { timeout: 8000 }); expect(router.currentRoute.value.params.id).toBe('gen-1')
  })

  it('Stop calls the backend cancellation; a cancelled generation offers to continue with the saved questions', async () => {
    let push!: (e: any) => void; let finish!: () => void
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise<void>((r) => (finish = r)) })
    const cancel = vi.spyOn(api.assessmentApi, 'cancelGeneration').mockResolvedValue(gstate())
    vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(gstate({ status: 'cancelled', completed: 3, progress: 30, last_seq: 9 }))
    const { wrapper } = await setup(vi.fn().mockResolvedValue(generating()))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('Stop'))!.trigger('click'); await flushPromises()
    expect(cancel).toHaveBeenCalledWith('gen-1')
    push(savedEv(4, 3)); push(ev(5, 'cancelled', { status: 'cancelled', completed: 3, progress: 30, message: 'Generation was stopped.' })); finish(); await flushPromises(); await flushPromises()
    expect(wrapper.text()).toContain('3 of 10 questions were saved'); expect(wrapper.findAll('button').some((b) => b.text() === 'Continue with 3')).toBe(true)
    expect(startBtn(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('an error event is shown as a persistent alert (not a timer or a toast) and keeps what was saved', async () => {
    let push!: (e: any) => void; let finish!: () => void
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise<void>((r) => (finish = r)) })
    vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(gstate({ status: 'failed', completed: 1, error: 'timeout', last_seq: 6 }))
    const { wrapper } = await setup(vi.fn().mockResolvedValue(generating()))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    push(ev(4, 'error', { status: 'failed', reason: 'timeout', message: 'The AI service took too long.' })); finish(); await flushPromises(); await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('took too long'); expect(wrapper.text()).toContain('1 of 10 questions are saved')
  })

  it('after a refresh the running generation is found and followed again', async () => {
    const getSse = vi.spyOn(sse, 'getSse').mockImplementation(() => new Promise(() => {}))
    const { wrapper } = await setup()
    vi.spyOn(api.assessmentApi, 'activeGeneration').mockResolvedValue({ active: generating({ generation: gstate({ completed: 4, progress: 40, last_seq: 11 }) }) })
    wrapper.unmount()
    const m = await mountApp(AssessmentsPage, { route: '/assessments' }); await flushPromises()
    expect(m.wrapper.get('[data-testid=generation-count]').text()).toBe('4 of 10'); expect(getSse).toHaveBeenCalledWith('/api/v1/assessments/gen-1/events', expect.objectContaining({ lastEventId: 11 }))
  })
})
