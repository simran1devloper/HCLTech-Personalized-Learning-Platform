import { describe, expect, it, vi, afterEach } from 'vitest'
import { effectScope } from 'vue'
import { mount } from '@vue/test-utils'
import { applyStage, formatMs, stateOf, summarize, type PipelineStage } from './pipeline'
import { usePipeline } from './usePipeline'
import PipelineProgress from './PipelineProgress.vue'
import { useQuizStream } from '@/features/study/useQuizStream'

const st = (id: string, status: PipelineStage['status'] = 'pending', extra: Partial<PipelineStage> = {}): PipelineStage => ({ id, label: id.toUpperCase(), status, ...extra })

describe('applyStage reducer', () => {
  it('updates in place, preserves order, appends unknown stages', () => {
    let s = [st('a'), st('b')]
    s = applyStage(s, st('a', 'running', { detail: 'working' })); s = applyStage(s, st('c', 'running'))
    expect(s.map((x) => x.id)).toEqual(['a', 'b', 'c']); expect(s[0]).toMatchObject({ status: 'running', detail: 'working', label: 'A' })
  })
  it('a finished stage is never reverted by a stale event, but may be retried', () => {
    let s = applyStage([st('a', 'done', { duration_ms: 10 })], st('a', 'running'))
    expect(s[0].status).toBe('done')
    s = applyStage(s, st('a', 'retrying', { attempt: 2 })); expect(s[0]).toMatchObject({ status: 'retrying', attempt: 2 })
  })
  it('summarize / stateOf / formatMs', () => {
    const s = [st('a', 'done'), st('b', 'failed'), st('c', 'running'), st('d')]
    expect(summarize(s)).toMatchObject({ total: 4, done: 1, failed: 1, finished: 2 }); expect(summarize(s).active.map((x) => x.id)).toEqual(['c'])
    expect(stateOf(true, s, false)).toBe('running'); expect(stateOf(false, [st('a', 'done')], false)).toBe('completed')
    expect(stateOf(false, s, false)).toBe('attention'); expect(stateOf(false, s, true)).toBe('cancelled'); expect(stateOf(false, [], false)).toBe('idle')
    expect([formatMs(850), formatMs(2400), formatMs(125000)]).toEqual(['850 ms', '2.4 s', '2m 5s'])
  })
})

describe('usePipeline', () => {
  afterEach(() => vi.useRealTimers())
  it('tracks elapsed time while running and stops the timer on end', () => {
    vi.useFakeTimers(); const p = effectScope().run(() => usePipeline())!
    p.begin(); vi.advanceTimersByTime(3000); expect(p.elapsedMs.value).toBeGreaterThanOrEqual(2750); expect(p.state.value).toBe('running')
    p.end(); const at = p.elapsedMs.value; vi.advanceTimersByTime(5000); expect(p.elapsedMs.value).toBe(at)
  })
  it('failUnfinished marks only unfinished stages (the UI never shows a stuck spinner after an abort)', () => {
    const p = effectScope().run(() => usePipeline())!
    p.setPlan([st('a'), st('b'), st('c')]); p.onStage(st('a', 'done')); p.onStage(st('b', 'running'))
    p.failUnfinished('Stopped'); expect(p.stages.value.map((s) => s.status)).toEqual(['done', 'failed', 'failed'])
  })
})

describe('PipelineProgress', () => {
  const stages = [st('plan', 'done', { duration_ms: 12, detail: '10 questions planned' }), st('generate', 'retrying', { attempt: 2, max_attempts: 2, detail: 'Revising after review feedback' }), st('review'), st('sign', 'failed')]
  it('renders every checkpoint with status text, attempt, detail, duration — not colour alone', () => {
    const w = mount(PipelineProgress, { props: { title: 'Quiz generation pipeline', stages, state: 'running', elapsedMs: 4200 } })
    const rows = w.findAll('li[data-stage]'); expect(rows.map((r) => r.attributes('data-status'))).toEqual(['done', 'retrying', 'pending', 'failed'])
    expect(rows[0].text()).toContain('Done'); expect(rows[0].text()).toContain('12 ms'); expect(rows[0].text()).toContain('10 questions planned')
    expect(rows[1].text()).toContain('Retrying'); expect(rows[1].text()).toContain('attempt 2 of 2'); expect(rows[2].text()).toContain('Waiting'); expect(rows[3].text()).toContain('Failed')
    expect(w.text()).toContain('Running'); expect(w.text()).toContain('1 / 4 steps'); expect(w.get('section').attributes('aria-busy')).toBe('true')
    expect(w.get('[role=status]').text()).toContain('Generate: Retrying'.replace('Generate', 'GENERATE'))   // live region announces the active step
  })
  it('collapses itself when a run completes and can be reopened', async () => {
    const w = mount(PipelineProgress, { props: { title: 'T', stages: [st('a', 'running')], state: 'running', elapsedMs: 0 } })
    const hidden = () => (w.get('ol').attributes('style') ?? '').includes('display: none')
    expect(hidden()).toBe(false)
    await w.setProps({ stages: [st('a', 'done')], state: 'completed' }); await new Promise((r) => setTimeout(r, 0)); expect(hidden()).toBe(true)
    expect(w.get('button[aria-expanded]').attributes('aria-expanded')).toBe('false')
    await w.get('button[aria-expanded]').trigger('click'); expect(hidden()).toBe(false)
  })
  it('shows an attention message when a step failed', () => {
    expect(mount(PipelineProgress, { props: { title: 'T', stages, state: 'attention', elapsedMs: 0 } }).text()).toContain('Some steps did not finish')
  })
})

describe('useQuizStream drives the pipeline from real SSE events', () => {
  const sse = (events: [string, object][]) => events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
  const ok = (text: string) => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(text)); c.close() } }), { status: 200 })
  const plan = [st('plan'), st('generate'), st('review'), st('sign')]
  it('success: stages complete and the quiz is returned', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([['meta', { stages: plan }], ['stage', st('plan', 'running')], ['stage', st('plan', 'done', { duration_ms: 3 })], ['stage', st('generate', 'done', { attempt: 1 })],
      ['stage', st('review', 'done')], ['stage', st('sign', 'done')], ['done', { quiz: { topic: 'Py', questions: [{ question: 'q', options: ['a', 'b', 'c', 'd'], correct_answer: 'a' }], quiz_token: 't' } }]]))))
    const q = effectScope().run(() => useQuizStream())!
    const quiz = await q.run('Py')
    expect(quiz?.quiz_token).toBe('t'); expect(q.state.value).toBe('completed'); expect(q.stages.value.every((s) => s.status === 'done')).toBe(true); expect(q.error.value).toBeNull()
  })
  it('server error: failed stage is shown, unfinished stages are failed, error message exposed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([['meta', { stages: plan }], ['stage', st('plan', 'done')], ['stage', st('generate', 'failed', { detail: 'The AI service did not respond.' })], ['error', { message: 'The AI service is unavailable. Please try again.' }]]))))
    const q = effectScope().run(() => useQuizStream())!
    expect(await q.run('Py')).toBeNull(); expect(q.state.value).toBe('attention'); expect(q.error.value).toMatch(/unavailable/)
    expect(q.stages.value.map((s) => s.status)).toEqual(['done', 'failed', 'failed', 'failed'])
  })
  it('stop() aborts the request and marks the run as stopped', async () => {
    let signal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { signal = init.signal!; return new Promise((_, rej) => signal.addEventListener('abort', () => rej(new DOMException('a', 'AbortError')))) }))
    const q = effectScope().run(() => useQuizStream())!
    const p = q.run('Py'); await Promise.resolve(); q.stop()
    expect(signal.aborted).toBe(true); expect(await p).toBeNull(); expect(q.state.value).toBe('cancelled'); expect(q.running.value).toBe(false)
  })
})

describe('stage icons', () => {
  it('section stages use the backend token, other checkpoints a fixed icon, unknown tokens fall back', async () => {
    const { stageIcon } = await import('./icons')
    expect(stageIcon({ id: 'section:key_concepts', icon: 'key-round' })).toBeTruthy()
    expect(stageIcon({ id: 'plan' })).toBeTruthy(); expect(stageIcon({ id: 'generate' })).toBeNull()
    expect(stageIcon({ id: 'section:x', icon: 'definitely-not-an-icon' })).toBe(stageIcon({ id: 'section:y', icon: 'file-text' }))
  })
  it('PipelineProgress renders an icon next to labelled steps that have one', () => {
    const w = mount(PipelineProgress, { props: { title: 'T', stages: [st('section:summary', 'done', { icon: 'file-text' }), st('generate', 'pending')], state: 'running', elapsedMs: 0 } })
    expect(w.findAll('li[data-stage]')[0].findAll('[data-testid=stage-icon]')).toHaveLength(1)
    expect(w.findAll('li[data-stage]')[1].findAll('[data-testid=stage-icon]')).toHaveLength(0)
  })
})
