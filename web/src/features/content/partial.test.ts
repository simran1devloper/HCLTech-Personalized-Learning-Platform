import { describe, expect, it } from 'vitest'
import { applyChunk, type ChunkEvent } from './partial'

const md = (seq: number, delta: string, attempt = 1): ChunkEvent => ({ name: 'deep_dive', attempt, seq, kind: 'md', delta })
const item = (seq: number, it: unknown, attempt = 1): ChunkEvent => ({ name: 'flashcards', attempt, seq, kind: 'item', item: it })

describe('applyChunk', () => {
  it('appends markdown deltas in order', () => {
    let p = applyChunk(undefined, md(0, '## Hi\n'))
    p = applyChunk(p, md(1, 'more\n'))
    expect(p).toMatchObject({ text: '## Hi\nmore\n', seq: 1, attempt: 1 })
  })
  it('collects list items', () => {
    let p = applyChunk(undefined, item(0, { question: 'q1', answer: 'a1' }))
    p = applyChunk(p, item(1, { question: 'q2', answer: 'a2' }))
    expect(p!.items).toEqual([{ question: 'q1', answer: 'a1' }, { question: 'q2', answer: 'a2' }])
  })
  it('ignores duplicate and out-of-order chunks (a reconnect or replay can never double the text)', () => {
    let p = applyChunk(undefined, md(0, 'A')); p = applyChunk(p, md(1, 'B')); p = applyChunk(p, md(1, 'B')); p = applyChunk(p, md(0, 'A'))
    expect(p!.text).toBe('AB')
  })
  it('a retry (new attempt) starts from scratch and a late chunk of the old attempt is dropped', () => {
    let p = applyChunk(undefined, md(0, 'bad draft', 1)); p = applyChunk(p, md(0, 'fresh ', 2)); p = applyChunk(p, md(1, 'start', 2))
    expect(p).toMatchObject({ text: 'fresh start', attempt: 2 })
    expect(applyChunk(p, md(2, 'stale', 1))).toBe(p)
  })
})
