import { API_BASE, ApiError, getToken, parseError } from './http'

export interface SseEvent { event: string; data: any; id?: string }

/** Incremental parser for text/event-stream. Feed arbitrary chunks; yields complete events. */
export class SseParser {
  private buf = ''
  push(chunk: string): SseEvent[] {
    this.buf += chunk.replace(/\r\n/g, '\n')
    const out: SseEvent[] = []
    let i: number
    while ((i = this.buf.indexOf('\n\n')) !== -1) {
      const block = this.buf.slice(0, i)
      this.buf = this.buf.slice(i + 2)
      let event = 'message'
      let id: string | undefined
      const data: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith(':')) continue // comment / keep-alive
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('id:')) id = line.slice(3).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
      }
      if (!data.length) continue
      const raw = data.join('\n')
      try { out.push({ event, data: JSON.parse(raw), id }) } catch { out.push({ event, data: raw, id }) }
    }
    return out
  }
}

export interface StreamOptions { signal: AbortSignal; onEvent: (e: SseEvent) => void }

/** POST + SSE over fetch (EventSource cannot send POST bodies or Authorization headers). Aborting `signal`
 *  closes the underlying connection, so no reader is leaked on unmount/navigation. */
export function postSse(path: string, body: unknown, opts: StreamOptions): Promise<void> {
  return openSse(path, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }, opts)
}

/** GET + SSE over fetch (EventSource cannot send the Authorization header). `lastEventId` resumes after an event the caller already processed. */
export function getSse(path: string, opts: StreamOptions & { lastEventId?: number }): Promise<void> {
  return openSse(path, { method: 'GET', headers: opts.lastEventId ? { 'Last-Event-ID': String(opts.lastEventId) } : {} }, opts)
}

async function openSse(path: string, init: { method: string; body?: string; headers: Record<string, string> }, { signal, onEvent }: StreamOptions): Promise<void> {
  let res: Response
  try {
    res = await fetch(API_BASE + path, {
      method: init.method, signal, body: init.body,
      headers: { ...init.headers, Accept: 'text/event-stream', Authorization: `Bearer ${getToken() ?? ''}` },
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError('Unable to reach the server. Check your connection.', 0, 'network')
  }
  if (!res.ok || !res.body) {
    let data: unknown = null
    try { data = await res.json() } catch { /* */ }
    throw parseError(res.status, data)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  const parser = new SseParser()
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      for (const ev of parser.push(value)) onEvent(ev)
    }
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError('The connection was interrupted.', 0, 'stream_interrupted')
  } finally {
    reader.cancel().catch(() => {})
  }
}
