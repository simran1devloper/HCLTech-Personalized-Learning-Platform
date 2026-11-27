import { describe, expect, it } from 'vitest'
import { SseParser } from './sse'
import { parseError } from './http'

describe('SseParser', () => {
  it('parses events split across arbitrary chunk boundaries', () => {
    const p = new SseParser(); const wire = 'event: meta\ndata: {"conversation_id":"c1"}\n\nevent: token\ndata: {"text":"Hel"}\n\nevent: token\ndata: {"text":"lo"}\n\nevent: done\ndata: {}\n\n'
    const out = []
    for (let i = 0; i < wire.length; i += 7) out.push(...p.push(wire.slice(i, i + 7)))
    expect(out.map((e) => e.event)).toEqual(['meta', 'token', 'token', 'done'])
    expect(out.filter((e) => e.event === 'token').map((e) => e.data.text).join('')).toBe('Hello')
  })
  it('handles CRLF, comments, multi-line data and non-JSON payloads', () => {
    const p = new SseParser()
    const out = p.push(': keep-alive\r\n\r\nevent: x\r\ndata: line1\r\ndata: line2\r\n\r\n')
    expect(out).toEqual([{ event: 'x', data: 'line1\nline2', id: undefined }])
  })
  it('does not emit an incomplete event', () => { expect(new SseParser().push('event: token\ndata: {"text":"a"}\n')).toEqual([]) })
})

describe('parseError', () => {
  it('reads v1 envelope and legacy detail shapes', () => {
    expect(parseError(404, { error: { code: 'not_found', message: 'nope', details: {} } })).toMatchObject({ message: 'nope', code: 'not_found', status: 404 })
    expect(parseError(401, { detail: 'Invalid email or password.' }).message).toBe('Invalid email or password.')
    expect(parseError(422, { detail: [{ loc: ['body'] }] }).code).toBe('validation_failed')
    expect(parseError(500, null).message).toMatch(/server/i)
  })
})

describe('SseParser ids', () => {
  it('exposes the event id so a reconnect can resume with Last-Event-ID', () => {
    const out = new SseParser().push('id: 12\nevent: question_saved\ndata: {"completed":3}\n\n')
    expect(out).toEqual([{ event: 'question_saved', data: { completed: 3 }, id: '12' }])
  })
})
