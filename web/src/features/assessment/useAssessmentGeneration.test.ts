import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import * as sse from '@/services/sse'
import * as api from '@/services/api'
import { ApiError } from '@/services/http'
import { useAssessmentGeneration } from './useAssessmentGeneration'
import type { GenerationEvent, GenerationState } from '@/types/api'

type Handler = (e: { event: string; data: any }) => void
const ev = (seq: number, type: string, o: Partial<GenerationEvent> = {}): { event: string; data: GenerationEvent } => ({
  event: type, data: { assessment_id: 'a1', type, seq, status: 'generating', completed: 0, requested: 4, progress: 0, timestamp: 't', ...o },
})
const saved = (seq: number, done: number) => ev(seq, 'question_saved', { completed: done, progress: done * 25, question: { question_id: `q${done}`, index: done - 1, concept: `C${done}`, difficulty: 2, source: 'generated' } })
const state = (o: Partial<GenerationState> = {}): GenerationState => ({ assessment_id: 'a1', status: 'generating', requested: 4, completed: 0, progress: 0, last_seq: 0, error: null, questions: [], ...o })
const setup = (onTerminal = vi.fn()) => { const scope = effectScope(); const g = scope.run(() => useAssessmentGeneration({ onTerminal }))!; return { g, scope, onTerminal } }
const tick = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => localStorage.setItem('learnai_token', 't'))
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear() })

describe('useAssessmentGeneration', () => {
  it('progress is saved/requested and moves only when a question_saved event arrives (activity is shown separately)', async () => {
    let push!: Handler; let finish!: () => void
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise<void>((r) => (finish = r)) })
    vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(state({ status: 'completed', completed: 4, progress: 100, last_seq: 9 }))
    const { g, onTerminal } = setup()
    g.attach({ assessment_id: 'a1', generation: state() })
    expect(g.progress.value).toBe(0)
    push(ev(1, 'generation_started', { stage: 'checking_bank' })); push(ev(2, 'generation_activity', { stage: 'generating', streamed: 3 }))
    expect(g.progress.value).toBe(0); expect(g.streamed.value).toBe(3)                      // 3 questions streamed by the model but none saved: still 0%
    push(saved(3, 1)); expect(g.progress.value).toBe(25); expect(g.completed.value).toBe(1); expect(g.streamed.value).toBe(0)
    push(saved(4, 2)); expect(g.progress.value).toBe(50)
    expect(g.slots.value.map((s) => s.state)).toEqual(['saved', 'saved', 'working', 'waiting'])
    push(saved(5, 3)); push(saved(6, 4)); push(ev(7, 'completed', { status: 'completed', completed: 4, progress: 100 })); finish(); await tick(); await tick()
    expect(g.progress.value).toBe(100); expect(g.status.value).toBe('completed'); expect(onTerminal).toHaveBeenCalledWith(expect.objectContaining({ status: 'completed', completed: 4 }))
  })

  it('replayed events after a reconnect are ignored (no double counting) and the stream resumes after the last sequence', async () => {
    vi.useFakeTimers()
    const calls: Array<{ lastEventId?: number }> = []; let push!: Handler; const ends: Array<() => void> = []
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { calls.push({ lastEventId: o.lastEventId }); push = o.onEvent; return new Promise<void>((r) => ends.push(r)) })
    const rec = vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(state({ completed: 2, progress: 50, last_seq: 4, questions: [] }))
    const { g } = setup()
    g.attach({ assessment_id: 'a1', generation: state() })
    push(saved(1, 1)); push(saved(2, 2))
    ends[0]()                                                       // the connection drops without a terminal event
    await vi.advanceTimersByTimeAsync(0); expect(g.reconnecting.value).toBe(true)
    await vi.advanceTimersByTimeAsync(600)                           // back-off, then recover persisted state and reconnect
    expect(rec).toHaveBeenCalled(); expect(calls).toHaveLength(2); expect(calls[1].lastEventId).toBeGreaterThanOrEqual(2)
    push(saved(2, 2)); push(saved(5, 3))                             // server replays seq 2 (already processed: recovered last_seq is 4), then a new one
    expect(g.completed.value).toBe(3); expect(g.reconnecting.value).toBe(false)
  })

  it('page refresh: attach(state) restores the counter and the saved question rows before any event arrives', () => {
    vi.spyOn(sse, 'getSse').mockImplementation(() => new Promise(() => {}))
    const { g } = setup()
    g.attach({ assessment_id: 'a1', generation: state({ completed: 2, progress: 50, last_seq: 7, questions: [
      { question_id: 'q1', index: 0, concept: 'A', difficulty: 1, question_type: 'single_choice', prompt: 'p', source: 'bank' }, { question_id: 'q2', index: 1, concept: 'B', difficulty: 2, question_type: 'single_choice', prompt: 'p', source: 'bank' }] }) })
    expect(g.completed.value).toBe(2); expect(g.progress.value).toBe(50); expect(g.slots.value[1]).toMatchObject({ state: 'saved', concept: 'B' }); expect(g.slots.value[2].state).toBe('working')
  })

  it('cancel calls the backend and ends in the cancelled state with the saved count kept', async () => {
    let push!: Handler; let finish!: () => void
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise<void>((r) => (finish = r)) })
    const cancel = vi.spyOn(api.assessmentApi, 'cancelGeneration').mockResolvedValue(state({ completed: 1, progress: 25 }))
    vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(state({ status: 'cancelled', completed: 1, progress: 25, last_seq: 5 }))
    const { g, onTerminal } = setup()
    g.attach({ assessment_id: 'a1', generation: state() }); push(saved(1, 1))
    const p = g.cancel(); await p
    expect(cancel).toHaveBeenCalledWith('a1'); expect(g.stopping.value).toBe(true)
    push(ev(2, 'cancelled', { status: 'cancelled', completed: 1, progress: 25, message: 'Generation was stopped.' })); finish(); await tick(); await tick()
    expect(g.status.value).toBe('cancelled'); expect(g.completed.value).toBe(1); expect(g.stopping.value).toBe(false); expect(onTerminal).toHaveBeenCalledWith(expect.objectContaining({ status: 'cancelled', completed: 1 }))
  })

  it('an error event ends in failed with the reason; a 404 on the stream fails instead of retrying forever', async () => {
    let push!: Handler; let finish!: () => void
    const spy = vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise<void>((r) => (finish = r)) })
    vi.spyOn(api.assessmentApi, 'generation').mockResolvedValue(state({ status: 'failed', completed: 0, error: 'provider' }))
    const a = setup(); a.g.attach({ assessment_id: 'a1', generation: state() })
    push(ev(1, 'error', { status: 'failed', reason: 'provider', message: 'The AI service is unavailable.' })); finish(); await tick(); await tick()
    expect(a.g.status.value).toBe('failed'); expect(a.g.error.value).toBe('provider'); expect(a.g.message.value).toContain('unavailable')
    spy.mockRejectedValue(new ApiError('not found', 404, 'not_found'))
    const b = setup(); b.g.attach({ assessment_id: 'a2', generation: state({ assessment_id: 'a2' }) }); await tick(); await tick()
    expect(b.g.status.value).toBe('failed'); expect(spy).toHaveBeenCalledTimes(2)
  })

  it('discarded questions are counted separately from progress', () => {
    let push!: Handler
    vi.spyOn(sse, 'getSse').mockImplementation((_p, o) => { push = o.onEvent; return new Promise(() => {}) })
    const { g } = setup(); g.attach({ assessment_id: 'a1', generation: state() })
    push(ev(1, 'question_validated', { valid: false, reason: 'invalid' })); push(ev(2, 'question_validated', { valid: false, reason: 'duplicate' }))
    expect(g.rejected.value).toBe(1); expect(g.duplicates.value).toBe(1); expect(g.progress.value).toBe(0)
  })
})
