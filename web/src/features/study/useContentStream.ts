import { onScopeDispose, reactive, ref } from 'vue'
import { postSse } from '@/services/sse'
import { ApiError } from '@/services/http'
import { usePipeline } from '@/features/pipeline/usePipeline'
import { applyChunk, type PartialSection } from '@/features/content/partial'
import type { LegacyContent } from '@/types/api'

export const SECTION_LABELS: Record<string, string> = {
  summary: 'Summary', key_concepts: 'Key concepts', deep_dive: 'Deep dive', real_world_applications: 'Real-world',
  viva_voice: 'Interview Q&A', practical_guide: 'Practical implementation guide', learning_roadmap: 'Roadmap', flashcards: 'Flashcards',
  recommendations: 'Recommendations', agent_intro: 'Tutor intro',
}

/** Streams study material section by section. Sections are usable as soon as they arrive; failed ones can be
 *  regenerated individually. Aborting (stop() or leaving the page) cancels generation on the server too. */
export function useContentStream() {
  const pipeline = usePipeline()
  const sections = reactive<Record<string, unknown>>({})
  const partial = reactive<Record<string, PartialSection>>({}) // live preview of sections still being written
  const failed = ref<string[]>([])
  const skipped = ref<string[]>([]) // deferred by the server's time budget (a subset of `failed`): generate on demand, not an error
  const pending = ref<string[]>([])
  const total = ref(0)
  const running = ref(false)
  const cached = ref(false)
  const error = ref<string | null>(null)
  const sessionId = ref<string | null>(null) // the saved history session this generation belongs to (created or reused by the server)
  let ctrl: AbortController | null = null

  function stop() {
    if (ctrl) { pipeline.failUnfinished('Stopped by you'); pipeline.end(true) }
    ctrl?.abort(); ctrl = null; running.value = false; pending.value = []
    for (const k of Object.keys(partial)) delete partial[k]
  }
  onScopeDispose(stop)

  async function run(body: { topic: string; level: string; score: number; session_id?: string; new_session?: boolean; diagnostic_session_id?: string }, only?: string[]): Promise<boolean> {
    stop()
    for (const k of Object.keys(partial)) delete partial[k]
    if (!only) { for (const k of Object.keys(sections)) delete sections[k]; failed.value = []; skipped.value = []; cached.value = false; sessionId.value = null }
    error.value = null; running.value = true; ctrl = new AbortController(); pipeline.begin()
    let done = false
    try {
      await postSse('/learning/generate-content/stream', only ? { ...body, sections: only } : body, {
        signal: ctrl.signal,
        onEvent: ({ event, data }) => {
          if (event === 'meta') { if (data.session_id) sessionId.value = data.session_id; if (data.stages) pipeline.setPlan(data.stages); total.value = only ? total.value || 10 : data.total; pending.value = [...data.sections]; cached.value = !!data.cached }
          else if (event === 'stage') pipeline.onStage(data)
          else if (event === 'chunk') { const next = applyChunk(partial[data.name], data); if (next) partial[data.name] = next }
          else if (event === 'section') {
            sections[data.name] = data.value
            delete partial[data.name]
            pending.value = pending.value.filter((n) => n !== data.name)
            failed.value = data.ok ? failed.value.filter((n) => n !== data.name) : [...new Set([...failed.value, data.name])]
            skipped.value = data.skipped ? [...new Set([...skipped.value, data.name])] : skipped.value.filter((n) => n !== data.name)
          } else if (event === 'done') { done = true; if (data.content) Object.assign(sections, pickSections(data.content)) }
          else if (event === 'error') error.value = data.message
        },
      })
    } catch (e) {
      if ((e as Error).name === 'AbortError') return false
      error.value = e instanceof ApiError ? e.message : 'The connection was interrupted.'
    } finally {
      running.value = false; ctrl = null
    }
    if (!done && !error.value) error.value = 'The connection closed before all sections finished.'
    if (!done) { failed.value = [...new Set([...failed.value, ...pending.value])]; pipeline.failUnfinished('Connection lost') }
    pending.value = []
    for (const k of Object.keys(partial)) delete partial[k] // an unfinished preview is never shown as if it were complete
    pipeline.end()
    return done
  }

  const completed = () => Object.keys(sections).filter((k) => !failed.value.includes(k)).length
  return { sections, partial, failed, skipped, pending, total, running, cached, error, sessionId, run, stop, completed, pipeline }
}

function pickSections(c: LegacyContent): Record<string, unknown> {
  const { topic: _t, level: _l, ...rest } = c as unknown as Record<string, unknown>
  delete rest.incomplete_sections
  return rest
}
