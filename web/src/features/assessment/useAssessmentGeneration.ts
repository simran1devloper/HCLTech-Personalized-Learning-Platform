import { computed, onScopeDispose, reactive, ref } from 'vue'
import { assessmentApi } from '@/services/api'
import { ApiError } from '@/services/http'
import { getSse } from '@/services/sse'
import type { Assessment, GenerationEvent, GenerationQuestion, GenerationState, GenerationStatus } from '@/types/api'

const TERMINAL: GenerationStatus[] = ['completed', 'failed', 'cancelled']
export const STAGE_LABELS: Record<string, string> = {
  checking_bank: 'Checking the question bank', generating: 'Generating missing questions', validating: 'Validating a question', saving: 'Saving a question',
  finalizing: 'Finishing up', completed: 'Done', error: 'Stopped', cancelled: 'Stopped',
}

/** Live view of one assessment generation. The server is the source of truth: counts, progress and question rows come from persisted events
 *  (`question_saved` etc.), never from timers. After a dropped connection or a page refresh the state is recovered from `GET /generation` and the stream resumes
 *  after the last processed sequence number. Model activity (`streamed`) is kept apart from progress because streamed questions are not validated or saved yet. */
export function useAssessmentGeneration(opts: { onTerminal?: (s: GenerationState) => void } = {}) {
  const id = ref<string | null>(null)
  const status = ref<GenerationStatus | 'idle'>('idle')
  const requested = ref(0)
  const completed = ref(0)
  const stage = ref('')
  const error = ref<string | null>(null)
  const message = ref<string | null>(null)
  const streamed = ref(0)          // questions the model has written in the current call (activity, NOT progress)
  const rejected = ref(0)
  const duplicates = ref(0)
  const reconnecting = ref(false)
  const stopping = ref(false)
  const saved = reactive<Record<number, GenerationQuestion>>({})
  let lastSeq = 0
  let ctrl: AbortController | null = null
  let run = 0                       // invalidates a superseded follow loop

  const active = computed(() => status.value === 'generating')
  // saved / requested: the only number that drives the bar
  const progress = computed(() => (requested.value ? Math.min(100, Math.round((completed.value / requested.value) * 1000) / 10) : 0))
  const slots = computed(() => Array.from({ length: requested.value }, (_, i) => {
    const q = saved[i]
    return q ? { index: i, state: 'saved' as const, concept: q.concept, difficulty: q.difficulty, source: q.source }
      : { index: i, state: status.value === 'generating' && i === completed.value ? ('working' as const) : ('waiting' as const), concept: '', difficulty: 0, source: '' }
  }))

  function reset() {
    id.value = null; status.value = 'idle'; requested.value = 0; completed.value = 0; stage.value = ''; error.value = null; message.value = null
    streamed.value = 0; rejected.value = 0; duplicates.value = 0; reconnecting.value = false; stopping.value = false; lastSeq = 0
    for (const k of Object.keys(saved)) delete saved[Number(k)]
  }

  function applyState(s: GenerationState) {
    status.value = s.status; requested.value = s.requested; completed.value = s.completed; error.value = s.error
    for (const q of s.questions) saved[q.index] = q
    lastSeq = Math.max(lastSeq, s.last_seq)
  }

  function apply(type: string, e: GenerationEvent) {
    if (e.seq <= lastSeq) return    // replay after a reconnect: already processed
    lastSeq = e.seq
    status.value = e.status; requested.value = e.requested; completed.value = e.completed
    if (e.stage) stage.value = e.stage
    if (type === 'question_saved' && e.question && typeof e.question.index === 'number') { saved[e.question.index] = e.question as GenerationQuestion; streamed.value = 0 }
    else if (type === 'generation_activity') streamed.value = e.streamed ?? 0
    else if (type === 'question_validated' && e.valid === false) { if (e.reason === 'duplicate') duplicates.value++; else rejected.value++ }
    else if (type === 'error' || type === 'cancelled') { message.value = e.message ?? null; if (type === 'error') error.value = e.reason ?? 'error' }
  }

  const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((res) => { const t = setTimeout(res, ms); signal.addEventListener('abort', () => { clearTimeout(t); res() }, { once: true }) })

  async function follow(myRun: number) {
    const c = ctrl = new AbortController()
    let attempt = 0
    while (myRun === run && !c.signal.aborted && status.value === 'generating') {
      try {
        await getSse(`/api/v1/assessments/${id.value}/events`, {
          signal: c.signal, lastEventId: lastSeq,
          onEvent: ({ event, data }) => { if (myRun === run && data && typeof data === 'object') { reconnecting.value = false; attempt = 0; apply(event, data as GenerationEvent) } },
        })
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) { status.value = 'failed'; error.value = e.message; break }
      }
      if (myRun !== run || c.signal.aborted || TERMINAL.includes(status.value as GenerationStatus)) break
      // the stream ended without a terminal event (network drop, proxy timeout, server restart): recover from the persisted state, then resume after lastSeq
      reconnecting.value = true
      await sleep(Math.min(8000, 500 * 2 ** attempt++), c.signal)
      if (c.signal.aborted || myRun !== run) return
      try { applyState(await assessmentApi.generation(id.value!, { signal: c.signal })) } catch (e) { if ((e as Error).name === 'AbortError') return }
    }
    if (myRun !== run) return
    reconnecting.value = false; stopping.value = false
    if (TERMINAL.includes(status.value as GenerationStatus)) {
      // the log is authoritative: reload the final state so counts and questions match the database exactly
      try { applyState(await assessmentApi.generation(id.value!)) } catch { /* keep what the events told us */ }
      opts.onTerminal?.({ assessment_id: id.value!, status: status.value as GenerationStatus, requested: requested.value, completed: completed.value, progress: progress.value, last_seq: lastSeq, error: error.value, questions: Object.values(saved) })
    }
  }

  /** Follow a generation started by `POST /assessments` (202) or found by `activeGeneration()` after a refresh. Safe to call twice for the same id. */
  function attach(a: Pick<Assessment, 'assessment_id' | 'generation'>) {
    if (id.value === a.assessment_id && active.value) return
    detach(); reset()
    id.value = a.assessment_id
    if (a.generation) applyState(a.generation); else status.value = 'generating'
    const myRun = ++run
    if (status.value === 'generating') void follow(myRun)
    else opts.onTerminal?.({ ...(a.generation as GenerationState) })
  }

  async function cancel() {
    if (!id.value || stopping.value || status.value !== 'generating') return
    stopping.value = true
    try { applyState(await assessmentApi.cancelGeneration(id.value)) } catch (e) { stopping.value = false; error.value = (e as Error).message; return }
    // the `cancelled` event normally arrives on the open stream; if the stream is gone, the persisted state above already says so
    if (status.value !== 'generating') { ctrl?.abort(); run++; stopping.value = false; opts.onTerminal?.({ assessment_id: id.value, status: status.value as GenerationStatus, requested: requested.value, completed: completed.value, progress: progress.value, last_seq: lastSeq, error: error.value, questions: Object.values(saved) }) }
  }

  function detach() { run++; ctrl?.abort(); ctrl = null }
  onScopeDispose(detach)

  return { id, status, requested, completed, progress, stage, error, message, streamed, rejected, duplicates, reconnecting, stopping, active, slots, attach, cancel, reset, detach }
}
