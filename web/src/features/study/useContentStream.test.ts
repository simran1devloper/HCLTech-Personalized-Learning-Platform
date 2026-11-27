import { describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { useContentStream } from './useContentStream'

const sse = (events: [string, object][]) => events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
const ok = (text: string) => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(text)); c.close() } }), { status: 200, headers: { 'content-type': 'text/event-stream' } })
const body = { topic: 'Python', level: 'Beginner', score: 3 }

describe('useContentStream', () => {
  it('fills sections progressively and records failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([
      ['meta', { sections: ['summary', 'flashcards'], total: 2, cached: false }],
      ['section', { name: 'summary', value: '## Hi', ok: true }],
      ['section', { name: 'flashcards', value: [], ok: false }],
      ['done', { incomplete_sections: ['flashcards'], content: null }],
    ]))))
    const s = effectScope().run(() => useContentStream())!
    expect(await s.run(body)).toBe(true)
    expect(s.sections.summary).toBe('## Hi'); expect(s.failed.value).toEqual(['flashcards']); expect(s.completed()).toBe(1); expect(s.running.value).toBe(false)
  })
  it('regenerating sends only the failed section names and clears them on success', async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok(sse([['meta', { sections: ['summary', 'flashcards'], total: 2 }], ['section', { name: 'summary', value: 'S', ok: true }], ['section', { name: 'flashcards', value: [], ok: false }], ['done', { content: null }]])))
      .mockResolvedValueOnce(ok(sse([['meta', { sections: ['flashcards'], total: 1 }], ['section', { name: 'flashcards', value: [{ question: 'q', answer: 'a' }], ok: true }], ['done', { content: null }]])))
    vi.stubGlobal('fetch', f)
    const s = effectScope().run(() => useContentStream())!
    await s.run(body); await s.run(body, ['flashcards'])
    expect(JSON.parse((f.mock.calls[1] as any)[1].body)).toMatchObject({ sections: ['flashcards'] })
    expect(s.failed.value).toEqual([]); expect(s.sections.summary).toBe('S'); expect(s.completed()).toBe(2)
  })
  it('a dropped connection marks unfinished sections as failed (so they can be regenerated)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([['meta', { sections: ['summary', 'deep_dive'], total: 2 }], ['section', { name: 'summary', value: 'S', ok: true }]]))))
    const s = effectScope().run(() => useContentStream())!
    expect(await s.run(body)).toBe(false)
    expect(s.failed.value).toEqual(['deep_dive']); expect(s.error.value).toMatch(/closed/)
  })

  it('shows chunks as a live preview, replaces them with the validated section and never leaves a stale preview behind', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([
      ['meta', { sections: ['summary', 'flashcards'], total: 2 }],
      ['chunk', { name: 'summary', attempt: 1, seq: 0, kind: 'md', delta: '## Draft\n' }],
      ['chunk', { name: 'flashcards', attempt: 1, seq: 0, kind: 'item', item: { question: 'q1', answer: 'a1' } }],
      ['section', { name: 'summary', value: '## Final', ok: true }],
    ]))))
    const s = effectScope().run(() => useContentStream())!
    expect(await s.run(body)).toBe(false)               // connection closed before `done`
    expect(s.sections.summary).toBe('## Final')          // section event wins
    expect(s.partial.summary).toBeUndefined()
    expect(s.partial.flashcards).toBeUndefined()         // unfinished preview is dropped when the run ends without it completing
    expect(s.failed.value).toEqual(['flashcards'])
  })
  it('exposes the preview while the stream is open', async () => {
    let push!: (t: string) => void, close!: () => void
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({ start(c) { push = (t) => c.enqueue(new TextEncoder().encode(t)); close = () => c.close() } }), { status: 200 })))
    const s = effectScope().run(() => useContentStream())!
    const run = s.run(body)
    await vi.waitFor(() => expect(push).toBeTypeOf('function'))
    push(sse([['meta', { sections: ['summary'], total: 1 }], ['chunk', { name: 'summary', attempt: 1, seq: 0, kind: 'md', delta: 'Hello ' }], ['chunk', { name: 'summary', attempt: 1, seq: 1, kind: 'md', delta: 'world' }]]))
    await vi.waitFor(() => expect(s.partial.summary?.text).toBe('Hello world'))
    push(sse([['section', { name: 'summary', value: 'Hello world!', ok: true }], ['done', { content: null }]])); close()
    expect(await run).toBe(true); expect(s.partial.summary).toBeUndefined(); expect(s.sections.summary).toBe('Hello world!')
  })
  it('tracks sections the server deferred for its time budget separately from real failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([
      ['meta', { sections: ['summary', 'deep_dive', 'flashcards'], total: 3 }],
      ['section', { name: 'summary', value: 'S', ok: true }],
      ['section', { name: 'deep_dive', value: 'placeholder', ok: false, skipped: true }],
      ['section', { name: 'flashcards', value: [], ok: false }],
      ['done', { incomplete_sections: ['deep_dive', 'flashcards'], content: null }],
    ]))))
    const s = effectScope().run(() => useContentStream())!
    await s.run(body)
    expect(s.skipped.value).toEqual(['deep_dive']); expect(s.failed.value).toEqual(['deep_dive', 'flashcards'])
    vi.stubGlobal('fetch', vi.fn(async () => ok(sse([['meta', { sections: ['deep_dive'], total: 1 }], ['section', { name: 'deep_dive', value: 'D', ok: true }], ['done', { content: null }]]))))
    await s.run(body, ['deep_dive'])
    expect(s.skipped.value).toEqual([]); expect(s.failed.value).toEqual(['flashcards'])   // generating it on demand clears the deferral
  })
  it('leaving the page aborts the request', async () => {
    let signal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { signal = init.signal!; return new Promise(() => {}) }))
    const scope = effectScope(); const s = scope.run(() => useContentStream())!
    void s.run(body); await Promise.resolve()
    scope.stop(); expect(signal.aborted).toBe(true); expect(s.running.value).toBe(false)
  })
})
