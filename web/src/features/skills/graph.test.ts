import { describe, expect, it } from 'vitest'
import { EDGES, NODES, edge, node } from './fixtures'
import { MAX_GRAPH_NODES, NO_FILTERS, NO_HIGHLIGHT, applyFilters, bestMatch, buildIndex, edgeKind, filtersActive, highlightFor, matchesQuery, neighbourhood, reach, roleOf, searchMatches, topologyKey, visibleSubgraph } from './graph'

const ix = buildIndex(NODES, EDGES)
const ids = (s: Iterable<string>) => [...s].sort()

describe('graph index (dynamic node & edge generation)', () => {
  it('builds adjacency from the payload and ignores edges to unknown nodes', () => {
    const i = buildIndex([node('a'), node('b')], [edge('a', 'b'), edge('ghost', 'b'), edge('a', 'ghost')])
    expect(i.up.get('b')).toEqual(['a']); expect(i.down.get('a')).toEqual(['b']); expect(i.byId.size).toBe(2)
  })
  it('reach follows direction transitively and excludes the start', () => {
    expect(ids(reach(ix.up, 'rag'))).toEqual(['llm', 'ml', 'pg', 'py', 'sql'])
    expect(ids(reach(ix.down, 'ml'))).toEqual(['llm', 'prompt', 'rag'])
    expect(reach(ix.up, 'py').size).toBe(0)
  })
  it('terminates on cyclic data and respects the bound', () => {
    const c = buildIndex([node('a'), node('b')], [edge('a', 'b'), edge('b', 'a')])
    expect(ids(reach(c.down, 'a'))).toEqual(['b'])
    expect(reach(ix.up, 'rag', 2).size).toBe(2)
  })
})

describe('selection highlighting', () => {
  it('marks the prerequisite path, downstream skills and unmet prerequisites', () => {
    const h = highlightFor(ix, 'llm')
    expect(h.selected).toBe('llm'); expect(ids(h.up)).toEqual(['ml', 'py']); expect(ids(h.down)).toEqual(['prompt', 'rag']); expect(ids(h.unmet)).toEqual(['ml'])
  })
  it('has no highlight without (or with an unknown) selection', () => {
    expect(highlightFor(ix, null)).toBe(NO_HIGHLIGHT); expect(highlightFor(ix, 'nope')).toBe(NO_HIGHLIGHT)
  })
  it('dims everything unrelated to the selection', () => {
    const h = highlightFor(ix, 'llm')
    expect(roleOf('llm', h, null)).toBe('selected'); expect(roleOf('py', h, null)).toBe('upstream'); expect(roleOf('rag', h, null)).toBe('downstream')
    expect(roleOf('sql', h, null)).toBe('dimmed'); expect(roleOf('pg', h, null)).toBe('dimmed')
  })
  it('classifies edges: prerequisite path, unmet prerequisite, downstream, dimmed', () => {
    const h = highlightFor(ix, 'llm'); const k = (s: string, t: string) => edgeKind(EDGES.find((e) => e.source === s && e.target === t)!, h)
    expect(k('py', 'ml')).toBe('path-up'); expect(k('ml', 'llm')).toBe('unmet'); expect(k('llm', 'rag')).toBe('path-down'); expect(k('sql', 'pg')).toBe('dimmed')
  })
  it('without a selection only unmet prerequisite edges stand out', () => {
    expect(edgeKind(edge('a', 'b', true), NO_HIGHLIGHT)).toBe('normal'); expect(edgeKind(edge('a', 'b', false), NO_HIGHLIGHT)).toBe('unmet')
  })
})

describe('search and filters', () => {
  it('search matches name, slug and category case-insensitively; empty search matches nothing special', () => {
    expect(ids(searchMatches(NODES, 'rag')!)).toEqual(['rag']); expect(ids(searchMatches(NODES, 'POSTGRE')!)).toEqual(['pg']); expect(searchMatches(NODES, '   ')).toBeNull()
    expect(matchesQuery(NODES[0], 'programming')).toBe(true)
  })
  it('search dims in the graph (roleOf) instead of removing nodes', () => {
    const m = searchMatches(NODES, 'sql'); expect(roleOf('sql', NO_HIGHLIGHT, m)).toBe('normal'); expect(roleOf('py', NO_HIGHLIGHT, m)).toBe('dimmed')
  })
  it('bestMatch prefers an exact name, then a prefix', () => {
    expect(bestMatch(NODES, 'sql')?.skill_id).toBe('sql'); expect(bestMatch(NODES, 'prompt')?.skill_id).toBe('prompt'); expect(bestMatch(NODES, 'zzz')).toBeUndefined()
    expect(bestMatch([node('a', { name: 'API Development' }), node('b', { name: 'API' })], 'api')?.skill_id).toBe('b')
  })
  it('category and status filters combine', () => {
    expect(ids(applyFilters(NODES, { ...NO_FILTERS, category: 'ai' }).map((n) => n.skill_id))).toEqual(['llm', 'prompt', 'rag'])
    expect(ids(applyFilters(NODES, { ...NO_FILTERS, statuses: ['mastered', 'ready'] }).map((n) => n.skill_id))).toEqual(['ml', 'py'])
    expect(applyFilters(NODES, { q: '', category: 'ai', statuses: ['mastered'] })).toEqual([])
    expect(filtersActive(NO_FILTERS)).toBe(false); expect(filtersActive({ ...NO_FILTERS, q: 'x' })).toBe(true)
  })
})

describe('visible subgraph', () => {
  it('keeps only edges whose both ends are visible', () => {
    const v = visibleSubgraph(NODES, EDGES, ix, { ...NO_FILTERS, category: 'ai' }, null)
    expect(ids(v.nodes.map((n) => n.skill_id))).toEqual(['llm', 'prompt', 'rag']); expect(v.edges.map((e) => `${e.source}>${e.target}`).sort()).toEqual(['llm>prompt', 'llm>rag'])
  })
  it('focus mode shows the selection with its prerequisites and dependents only', () => {
    const v = visibleSubgraph(NODES, EDGES, ix, NO_FILTERS, 'pg')
    expect(ids(v.nodes.map((n) => n.skill_id))).toEqual(['pg', 'py', 'rag', 'sql'])
    expect(neighbourhood(ix, 'pg').has('prompt')).toBe(false)
  })
  it('caps very large graphs, keeping the focused neighbourhood, and reports what was hidden', () => {
    const big = Array.from({ length: 1000 }, (_, i) => node(`n${i}`, { depth: i % 7, name: `Skill ${String(i).padStart(4, '0')}` }))
    const bi = buildIndex(big, [])
    const v = visibleSubgraph(big, [], bi, NO_FILTERS, null)
    expect(v.nodes).toHaveLength(MAX_GRAPH_NODES); expect(v.truncated).toBe(700)
    expect(visibleSubgraph(big, [], bi, NO_FILTERS, null, 50).nodes).toHaveLength(50)
  })
  it('stays fast for thousands of nodes (performance guard)', () => {
    const big = Array.from({ length: 4000 }, (_, i) => node(`n${i}`, { depth: i % 20 }))
    const edges = big.slice(1).map((n, i) => edge(`n${i}`, n.skill_id))
    const t = performance.now(); const i = buildIndex(big, edges); highlightFor(i, 'n2000'); visibleSubgraph(big, edges, i, NO_FILTERS, 'n2000'); expect(performance.now() - t).toBeLessThan(500)
  })
})

describe('layout cache key', () => {
  it('depends on topology only: order-independent, and ignores status', () => {
    expect(topologyKey(['b', 'a'], [edge('a', 'b')])).toBe(topologyKey(['a', 'b'], [edge('a', 'b', false)]))
    expect(topologyKey(['a', 'b'], [])).not.toBe(topologyKey(['a', 'b'], [edge('a', 'b')]))
  })
})
