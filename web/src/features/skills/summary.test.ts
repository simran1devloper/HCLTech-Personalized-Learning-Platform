import { describe, expect, it } from 'vitest'
import { NODES, node } from './fixtures'
import { criticalGaps, hoursToTargets, nextMilestone } from './summary'

describe('career summary derivations', () => {
  it('critical gaps are unmastered critical targets, most foundational first', () => {
    expect(criticalGaps(NODES).map((n) => n.skill_id)).toEqual(['llm', 'rag'])
  })
  it('sums remaining study hours over targets', () => { expect(hoursToTargets(NODES)).toBe(0 + 9 + 16 + 12 + 6) })
  it('next milestone: the most foundational critical target, with its missing prerequisites and total hours', () => {
    const m = nextMilestone(NODES)!
    expect(m.node.skill_id).toBe('llm'); expect(m.prerequisites.map((p) => p.skill_id)).toEqual(['ml']); expect(m.hours).toBe(16 + 40)
  })
  it('falls back to non-critical targets, and is null when everything is mastered', () => {
    expect(nextMilestone([node('a', { is_target: true, status: 'ready' })])?.node.skill_id).toBe('a')
    expect(nextMilestone([node('a', { is_target: true, status: 'mastered' })])).toBeNull()
  })
})
