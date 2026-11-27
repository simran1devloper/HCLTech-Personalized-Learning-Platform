import { onScopeDispose, ref, shallowRef } from 'vue'
import { postSse } from '@/services/sse'
import { ApiError } from '@/services/http'
import { tutorApi } from '@/services/api'
import type { TutorChatOptions, TutorSource, TutorTool } from '@/types/api'

export type StreamState = 'idle' | 'connecting' | 'streaming' | 'error'
export interface StreamError { message: string; code: string; partial: boolean; retryable: boolean }
export type TutorRequest = TutorChatOptions & { message?: string; conversation_id?: string | null; client_message_id?: string }
/** How long Stop waits for the server to confirm before the connection is simply dropped. */
export const STOP_GRACE_MS = 2500

/** Stage names sent by the server; shown as they happen (no invented progress percentages). */
export const STAGES: Record<string, string> = {
  processing: 'Processing your question…', retrieving_context: 'Retrieving your learning context…',
  executing_tool: 'Running a LearnAI action…', generating: 'Generating the response…',
}

/** Random id that makes one question idempotent: retrying it never stores it twice. */
export const newMessageId = () => globalThis.crypto?.randomUUID?.() ?? `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`

/** SSE streaming for one tutor turn. Tokens are buffered and flushed once per animation frame, so a fast stream
 *  causes at most one reactive update per frame. The AbortController is aborted on stop() and on scope disposal. */
export function useTutorStream() {
  const state = ref<StreamState>('idle')
  const text = ref('')
  const conversationId = ref<string | null>(null)
  const messageId = ref<number | null>(null)
  const stage = ref<string | null>(null)
  const sources = shallowRef<TutorSource[]>([])
  const tools = shallowRef<TutorTool[]>([])
  const error = shallowRef<StreamError | null>(null)
  let ctrl: AbortController | null = null
  let buf = ''; let raf = 0; let grace: ReturnType<typeof setTimeout> | undefined

  const flush = () => { raf = 0; if (buf) { text.value += buf; buf = '' } }
  const schedule = () => { if (!raf) raf = requestAnimationFrame(flush) }
  function abort() { clearTimeout(grace); ctrl?.abort(); ctrl = null; cancelAnimationFrame(raf); flush(); if (state.value !== 'error') state.value = 'idle' }
  /** Ask the server to stop (it saves the partial answer and closes the stream); drop the connection if that fails. */
  async function stop() {
    const id = messageId.value, current = ctrl
    if (!current) return
    if (id === null) return abort()
    clearTimeout(grace); grace = setTimeout(() => { if (ctrl === current) abort() }, STOP_GRACE_MS)
    try { if (!(await tutorApi.stop(id)).stopping && ctrl === current) abort() } catch { if (ctrl === current) abort() }
  }
  onScopeDispose(() => { clearTimeout(grace); ctrl?.abort(); cancelAnimationFrame(raf) })

  /** Resolves with the final text on `done`; throws nothing (errors are exposed via `error`).
   *  `path` defaults to the chat endpoint; regeneration posts to the conversation's regenerate endpoint. */
  async function send(body: TutorRequest, opts: { retries?: number; path?: string } = {}): Promise<string | null> {
    abort(); text.value = ''; buf = ''; error.value = null; state.value = 'connecting'; messageId.value = null; stage.value = null; sources.value = []; tools.value = []
    const retries = opts.retries ?? 1
    const request = opts.path ? body : { ...body, client_message_id: body.client_message_id ?? newMessageId(), conversation_id: body.conversation_id ?? conversationId.value }
    for (let attempt = 0; attempt <= retries; attempt++) {
      const mine = (ctrl = new AbortController())
      let finished = false, cancelled = false, serverError: StreamError | null = null, gotTokens = false
      try {
        await postSse(opts.path ?? '/api/v1/tutor/chat', request, {
          signal: mine.signal,
          onEvent: ({ event, data }) => {
            if (event === 'meta') { conversationId.value = data.conversation_id; messageId.value = data.message_id ?? null }
            else if (event === 'status') stage.value = data.stage
            else if (event === 'context') sources.value = data.sources ?? []
            else if (event === 'tool_result') tools.value = [...tools.value, data]
            else if (event === 'token') { gotTokens = true; state.value = 'streaming'; buf += data.text; schedule() }
            else if (event === 'done') finished = true
            else if (event === 'cancelled') cancelled = true
            else if (event === 'error') serverError = { message: data.message, code: data.code, partial: !!data.partial, retryable: true }
          },
        })
      } catch (e) {
        if ((e as Error).name === 'AbortError') return null
        cancelAnimationFrame(raf); flush()
        const net = e instanceof ApiError && (e.isNetwork || e.code === 'stream_interrupted')
        // Reconnect only if nothing was received yet (a retry would duplicate a half-delivered answer)
        if (net && !gotTokens && attempt < retries) { await new Promise((r) => setTimeout(r, 600 * (attempt + 1))); continue }
        clearTimeout(grace); ctrl = null
        error.value = { message: (e as Error).message, code: (e as ApiError).code ?? 'error', partial: gotTokens, retryable: net || (e as ApiError).status >= 500 || (e as ApiError).status === 409 }
        state.value = 'error'; return null
      }
      cancelAnimationFrame(raf); flush(); clearTimeout(grace); ctrl = null
      if (serverError) { error.value = serverError; state.value = 'error'; return null }
      if (cancelled) { state.value = 'idle'; return null }
      if (!finished) { error.value = { message: 'The connection closed before the answer finished.', code: 'stream_interrupted', partial: gotTokens, retryable: true }; state.value = 'error'; return null }
      state.value = 'idle'; return text.value
    }
    return null
  }
  function reset() { abort(); text.value = ''; error.value = null; conversationId.value = null; messageId.value = null; stage.value = null; sources.value = []; tools.value = []; state.value = 'idle' }
  return { state, text, conversationId, messageId, stage, sources, tools, error, send, stop, reset }
}
