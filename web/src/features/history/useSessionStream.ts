import { onScopeDispose, ref } from 'vue'
import { postSse } from '@/services/sse'
import { ApiError } from '@/services/http'

export interface SectionEvent { name: string; value: unknown; ok: boolean; reused?: boolean; version?: number; title?: string }

/** Runs one generation request against a saved session (generate / regenerate / continue) and reports each section as it is persisted.
 *  Leaving the page or calling stop() aborts the request; the server keeps every section it had already saved, so "Continue" resumes cleanly. */
export function useSessionStream() {
  const running = ref(false)
  const error = ref<string | null>(null)
  const generating = ref<string[]>([])
  const detail = ref('')
  let ctrl: AbortController | null = null

  function stop() { ctrl?.abort(); ctrl = null; running.value = false; generating.value = [] }
  onScopeDispose(stop)

  /** Resolves true when the server reported `done`. Never throws. */
  async function run(path: string, body: unknown, onSection: (e: SectionEvent) => void): Promise<boolean> {
    stop()
    error.value = null; running.value = true; detail.value = ''; ctrl = new AbortController()
    let done = false
    try {
      await postSse(path, body ?? {}, {
        signal: ctrl.signal,
        onEvent: ({ event, data }) => {
          if (event === 'meta') generating.value = [...(data.generating ?? data.sections ?? [])]
          else if (event === 'stage' && data.id === 'plan' && data.detail) detail.value = data.detail
          else if (event === 'section') { generating.value = generating.value.filter((n) => n !== data.name); onSection(data) }
          else if (event === 'done') done = true
          else if (event === 'error') error.value = data.message
        },
      })
    } catch (e) {
      if ((e as Error).name === 'AbortError') return false
      error.value = e instanceof ApiError ? e.message : 'The connection was interrupted.'
    } finally { running.value = false; ctrl = null; generating.value = [] }
    if (!done && !error.value) error.value = 'The connection closed before generation finished. Your completed sections are saved — use Continue to resume.'
    return done
  }
  return { running, error, generating, detail, run, stop }
}
