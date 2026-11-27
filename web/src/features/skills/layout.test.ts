import { beforeEach, describe, expect, it } from 'vitest'
import { EDGES, NODES, edge } from './fixtures'
import { NODE_W, clearLayoutCache, computeLayout, fallbackLayout, layoutCacheSize } from './layout'

const input = { ids: NODES.map((n) => n.skill_id), edges: EDGES }
beforeEach(clearLayoutCache)

describe('ELK hierarchical layout', () => {
  it('positions every node, left to right by prerequisite depth', async () => {
    const p = await computeLayout(input)
    expect(p.size).toBe(NODES.length)
    for (const e of EDGES) expect(p.get(e.target)!.x).toBeGreaterThan(p.get(e.source)!.x)
    expect(p.get('rag')!.x).toBeGreaterThan(p.get('llm')!.x)
  })
  it('never overlaps nodes', async () => {
    const p = [...(await computeLayout(input)).values()]
    for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) expect(Math.abs(p[i].x - p[j].x) >= NODE_W || Math.abs(p[i].y - p[j].y) >= 60).toBe(true)
  })
  it('is deterministic and cached by topology (status changes do not re-layout)', async () => {
    const a = await computeLayout(input)
    expect(layoutCacheSize()).toBe(1)
    const b = await computeLayout({ ids: [...input.ids].reverse(), edges: EDGES.map((e) => ({ ...e, satisfied: !e.satisfied })) })
    expect(b).toBe(a); expect(layoutCacheSize()).toBe(1)
    clearLayoutCache(); const c = await computeLayout(input)
    expect([...c.entries()]).toEqual([...a.entries()])
  })
  it('lays out an empty and an edge-free graph', async () => {
    expect((await computeLayout({ ids: [], edges: [] })).size).toBe(0)
    expect((await computeLayout({ ids: ['a', 'b'], edges: [] })).size).toBe(2)
  })
  it('handles several hundred nodes in reasonable time', async () => {
    const ids = Array.from({ length: 400 }, (_, i) => `n${i}`); const edges = ids.slice(1).filter((_, i) => i % 3).map((id, i) => edge(`n${Math.floor(i / 2)}`, id))
    const t = performance.now(); const p = await computeLayout({ ids, edges }); expect(p.size).toBe(400); expect(performance.now() - t).toBeLessThan(5000)
  }, 10_000)
})

describe('fallback layout', () => {
  it('layers by longest path and survives cycles', () => {
    const p = fallbackLayout(input); expect(p.get('rag')!.x).toBeGreaterThan(p.get('llm')!.x); expect(p.get('py')!.x).toBe(0)
    expect(fallbackLayout({ ids: ['a', 'b'], edges: [edge('a', 'b'), edge('b', 'a')] }).size).toBe(2)
  })
})
