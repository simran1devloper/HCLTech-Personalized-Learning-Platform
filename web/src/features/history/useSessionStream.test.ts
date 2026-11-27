import { describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { useSessionStream } from './useSessionStream'

const sse = (events: [string, object][]) => events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
const resp = (text: string) => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(text)); c.close() } }), { status: 200 })

describe('useSessionStream', () => {
  it('reports each persisted section and resolves true on done', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => resp(sse([['meta', { generating: ['a', 'b'] }], ['section', { name: 'a', value: 'A', ok: true }], ['section', { name: 'b', value: 'B', ok: true }], ['done', {}]]))))
    const s = effectScope().run(() => useSessionStream())!; const got: string[] = []
    expect(await s.run('/x', {}, (e) => got.push(e.name))).toBe(true)
    expect(got).toEqual(['a', 'b']); expect(s.running.value).toBe(false); expect(s.error.value).toBeNull()
  })
  it('a dropped connection is reported as resumable and never throws', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => resp(sse([['meta', { generating: ['a', 'b'] }], ['section', { name: 'a', value: 'A', ok: true }]]))))
    const s = effectScope().run(() => useSessionStream())!
    expect(await s.run('/x', {}, () => {})).toBe(false)
    expect(s.error.value).toMatch(/saved — use Continue to resume/)
  })
  it('surfaces a network failure and a server error event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
    const s = effectScope().run(() => useSessionStream())!
    await s.run('/x', {}, () => {}); expect(s.error.value).toMatch(/Unable to reach the server/)
    vi.stubGlobal('fetch', vi.fn(async () => resp(sse([['error', { message: 'AI down' }]]))))
    await s.run('/x', {}, () => {}); expect(s.error.value).toBe('AI down')
  })
})
