import { describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { useTutorStream } from './useTutorStream'

const sse = (events: [string, object][]) => events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
function stream(chunks: string[], { hang = false } = {}) {
  const enc = new TextEncoder(); let i = 0
  return new ReadableStream({ pull(c) { if (i < chunks.length) c.enqueue(enc.encode(chunks[i++])); else if (!hang) c.close() } })
}
const ok = (body: ReadableStream) => new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
const msg = { message: 'hi', language: 'en' as const }

describe('useTutorStream', () => {
  it('assembles tokens split across chunks and finishes', async () => {
    const wire = sse([['meta', { conversation_id: 'c1' }], ['token', { text: 'Hel' }], ['token', { text: 'lo' }], ['done', { conversation_id: 'c1' }]])
    vi.stubGlobal('fetch', vi.fn(async () => ok(stream([wire.slice(0, 20), wire.slice(20, 55), wire.slice(55)]))))
    const s = effectScope().run(() => useTutorStream())!
    expect(await s.send(msg)).toBe('Hello'); expect(s.text.value).toBe('Hello'); expect(s.conversationId.value).toBe('c1'); expect(s.state.value).toBe('idle'); expect(s.error.value).toBeNull()
  })
  it('sends auth + conversation id and parses SSE from a POST', async () => {
    localStorage.setItem('learnai_token', 'T')
    const f = vi.fn(async () => ok(stream([sse([['meta', { conversation_id: 'c9' }], ['done', {}]])]))); vi.stubGlobal('fetch', f)
    const s = effectScope().run(() => useTutorStream())!; s.conversationId.value = 'c9'; await s.send(msg)
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toMatch(/\/api\/v1\/tutor\/chat$/); expect((init.headers as any).Authorization).toBe('Bearer T'); expect(JSON.parse(init.body as string).conversation_id).toBe('c9')
  })
  it('surfaces a server error event with partial text and a retry flag', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(stream([sse([['meta', { conversation_id: 'c' }], ['token', { text: 'part' }], ['error', { code: 'ai_provider_error', message: 'AI unavailable', partial: true }]])]))))
    const s = effectScope().run(() => useTutorStream())!
    expect(await s.send(msg)).toBeNull(); expect(s.state.value).toBe('error'); expect(s.error.value).toMatchObject({ message: 'AI unavailable', partial: true, retryable: true }); expect(s.text.value).toBe('part')
  })
  it('reconnects once when the connection fails before any token', async () => {
    const f = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(ok(stream([sse([['meta', { conversation_id: 'c' }], ['token', { text: 'ok' }], ['done', {}]])])))
    vi.stubGlobal('fetch', f)
    const s = effectScope().run(() => useTutorStream())!
    expect(await s.send(msg)).toBe('ok'); expect(f).toHaveBeenCalledTimes(2)
  })
  it('does NOT replay a request after tokens were delivered (no duplicated answer)', async () => {
    const enc = new TextEncoder(); let n = 0
    const body = new ReadableStream({ pull(c) { if (n++ === 0) c.enqueue(enc.encode(sse([['meta', { conversation_id: 'c' }], ['token', { text: 'half' }]]))); else c.error(new TypeError('reset')) } })
    const f = vi.fn(async () => ok(body)); vi.stubGlobal('fetch', f)
    const s = effectScope().run(() => useTutorStream())!
    expect(await s.send(msg)).toBeNull(); expect(f).toHaveBeenCalledTimes(1); expect(s.error.value).toMatchObject({ partial: true, code: 'stream_interrupted' }); expect(s.text.value).toBe('half')
  })
  it('reports a stream that ends without done as interrupted', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(stream([sse([['meta', { conversation_id: 'c' }], ['token', { text: 'x' }]])]))))
    const s = effectScope().run(() => useTutorStream())!; await s.send(msg); expect(s.error.value?.code).toBe('stream_interrupted')
  })
  it('maps HTTP errors from the v1 envelope (e.g. 404, 429) without retrying', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'rate limit exceeded', details: {} } }), { status: 429 })); vi.stubGlobal('fetch', f)
    const s = effectScope().run(() => useTutorStream())!; await s.send(msg)
    expect(s.error.value).toMatchObject({ code: 'rate_limited', message: 'rate limit exceeded' }); expect(f).toHaveBeenCalledTimes(1)
  })
  it('aborts the network request on stop() and when its scope is disposed (no leaked reader)', async () => {
    let signal!: AbortSignal
    // like a real fetch body, the stream errors with AbortError when the request signal aborts
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => {
      signal = init.signal as AbortSignal; const enc = new TextEncoder(); let sent = false
      return ok(new ReadableStream({ start(c) { signal.addEventListener('abort', () => c.error(new DOMException('aborted', 'AbortError'))) },
        pull(c) { if (!sent) { sent = true; c.enqueue(enc.encode(sse([['meta', { conversation_id: 'c' }]]))) } else return new Promise(() => {}) } }))
    }))
    const scope = effectScope(); const s = scope.run(() => useTutorStream())!
    const p = s.send(msg); await new Promise((r) => setTimeout(r, 20)); expect(signal.aborted).toBe(false)
    scope.stop(); expect(signal.aborted).toBe(true); expect(await p).toBeNull()
  })
})

describe('useTutorStream: assistant events and Stop', () => {
  it('collects status, sources and tool results, and treats cancelled as a normal end', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(stream([sse([['meta', { conversation_id: 'c', message_id: 7 }], ['status', { stage: 'executing_tool' }], ['context', { sources: [{ kind: 'goal', label: 'Career goal', link: '/' }] }],
      ['tool_result', { execution_id: 'e' }], ['token', { text: 'part' }], ['cancelled', { status: 'cancelled' }]])]))))
    const s = effectScope().run(() => useTutorStream())!
    expect(await s.send(msg)).toBeNull(); expect(s.error.value).toBeNull(); expect(s.state.value).toBe('idle'); expect(s.text.value).toBe('part')
    expect(s.messageId.value).toBe(7); expect(s.stage.value).toBe('executing_tool'); expect(s.sources.value).toHaveLength(1); expect(s.tools.value).toHaveLength(1)
  })
  it('asks the server to stop and lets it close the stream', async () => {
    const enc = new TextEncoder(); let ctl!: ReadableStreamDefaultController<Uint8Array>
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url)
      if (url.endsWith('/stop')) { ctl.enqueue(enc.encode(sse([['cancelled', {}]]))); ctl.close(); return new Response(JSON.stringify({ stopping: true }), { status: 200 }) }
      return ok(new ReadableStream({ start(c) { ctl = c; c.enqueue(enc.encode(sse([['meta', { conversation_id: 'c', message_id: 9 }], ['token', { text: 'a' }]]))) } }))
    }))
    const s = effectScope().run(() => useTutorStream())!
    const p = s.send(msg); await new Promise((r) => setTimeout(r, 30)); await s.stop()
    expect(await p).toBeNull(); expect(urls[1]).toMatch(/\/api\/v1\/tutor\/messages\/9\/stop$/); expect(s.error.value).toBeNull(); expect(s.state.value).toBe('idle')
  })
})
