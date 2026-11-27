import { describe, expect, it } from 'vitest'
import { parseBlocks } from './blocks'

describe('parseBlocks', () => {
  it('accepts every supported block type', () => {
    const r = parseBlocks([
      { type: 'text', markdown: 'hi' }, { type: 'code', language: 'python', code: 'print(1)' },
      { type: 'table', headers: ['a', 'b'], rows: [['1', '2']] }, { type: 'mermaid', source: 'flowchart TD\n A-->B' },
      { type: 'math', latex: 'x^2' }, { type: 'quiz', question_ids: ['q1'] }, { type: 'flashcards', cards: [{ front: 'a', back: 'b' }] },
      { type: 'case_study', title: 't', scenario: 's' }, { type: 'exercise', prompt: 'p' }, { type: 'tutorial', steps: ['one'] },
    ])
    expect(r.rejected).toEqual([]); expect(r.blocks).toHaveLength(10)
  })
  it('rejects unknown types, bad shapes and non-arrays without throwing', () => {
    const r = parseBlocks([{ type: 'script', src: 'x' }, { type: 'html', html: '<b>' }, { type: 'table', headers: ['a'], rows: [['1', '2']] }, { type: 'code', language: 'py thon!', code: 'x' }, null, 'str', { type: 'text', markdown: 'ok' }])
    expect(r.blocks).toEqual([{ type: 'text', markdown: 'ok' }]); expect(r.rejected).toHaveLength(6)
    expect(parseBlocks('nope').rejected[0].index).toBe(-1)
  })
  it('one bad block does not discard its valid neighbours', () => {
    expect(parseBlocks([{ type: 'text', markdown: 'a' }, { type: 'bogus' }, { type: 'text', markdown: 'b' }]).blocks).toHaveLength(2)
  })
})
