import type { GraphEdge, GraphNode, SkillStatus } from '@/types/api'

/** Pure graph logic for the Skill Explorer. Nothing here touches the DOM, the network or Vue, so it is cheap to test and to reuse. */

export interface GraphIndex { byId: Map<string, GraphNode>; up: Map<string, string[]>; down: Map<string, string[]> }
/** prerequisite -> dependent adjacency, built once per graph payload (O(V+E)); every later lookup is a map hit. */
export function buildIndex(nodes: GraphNode[], edges: GraphEdge[]): GraphIndex {
  const byId = new Map(nodes.map((n) => [n.skill_id, n]))
  const up = new Map<string, string[]>(), down = new Map<string, string[]>()
  for (const e of edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) continue // never draw an edge to a node that is not in the payload
    ;(up.get(e.target) ?? up.set(e.target, []).get(e.target)!).push(e.source)
    ;(down.get(e.source) ?? down.set(e.source, []).get(e.source)!).push(e.target)
  }
  return { byId, up, down }
}

/** Everything reachable from `start` through `adj` (excluding `start`), optionally bounded. Iterative: safe on deep or cyclic data. */
export function reach(adj: Map<string, string[]>, start: string, max = Infinity): Set<string> {
  const seen = new Set<string>(); const queue = [start]
  for (let i = 0; i < queue.length && seen.size < max; i++) for (const m of adj.get(queue[i]) ?? []) if (m !== start && !seen.has(m)) { seen.add(m); queue.push(m) }
  return seen
}

export interface Highlight { selected: string | null; up: Set<string>; down: Set<string>; unmet: Set<string> }
export const NO_HIGHLIGHT: Highlight = { selected: null, up: new Set(), down: new Set(), unmet: new Set() }
/** Prerequisite path (up), downstream skills (down) and the prerequisites the learner has not met yet (unmet). */
export function highlightFor(ix: GraphIndex, id: string | null | undefined): Highlight {
  const n = id ? ix.byId.get(id) : undefined
  if (!n) return NO_HIGHLIGHT
  const up = reach(ix.up, n.skill_id), down = reach(ix.down, n.skill_id)
  return { selected: n.skill_id, up, down, unmet: new Set(n.missing_prerequisites.filter((m) => up.has(m))) }
}

export type NodeRole = 'selected' | 'upstream' | 'downstream' | 'dimmed' | 'normal'
export function roleOf(id: string, h: Highlight, matches: Set<string> | null): NodeRole {
  if (h.selected) return id === h.selected ? 'selected' : h.up.has(id) ? 'upstream' : h.down.has(id) ? 'downstream' : 'dimmed'
  return matches && !matches.has(id) ? 'dimmed' : 'normal'
}
export type EdgeKind = 'path-up' | 'path-down' | 'unmet' | 'normal' | 'dimmed'
export function edgeKind(e: GraphEdge, h: Highlight): EdgeKind {
  if (h.selected) {
    const s = h.selected
    if ((e.target === s || h.up.has(e.target)) && h.up.has(e.source)) return e.satisfied ? 'path-up' : 'unmet'
    if ((e.source === s || h.down.has(e.source)) && h.down.has(e.target)) return 'path-down'
    return 'dimmed'
  }
  return e.satisfied ? 'normal' : 'unmet'
}

export interface Filters { q: string; category: string; statuses: SkillStatus[] }
export const NO_FILTERS: Filters = { q: '', category: '', statuses: [] }
export const filtersActive = (f: Filters) => !!(f.q.trim() || f.category || f.statuses.length)

const norm = (s: string) => s.trim().toLowerCase()
export function matchesQuery(n: GraphNode, q: string): boolean {
  const t = norm(q)
  return !t || n.name.toLowerCase().includes(t) || n.slug.includes(t) || n.category.toLowerCase().includes(t)
}
/** Category and status filters decide which nodes exist in the view. Search never hides anything in the graph: it dims. */
export function applyFilters(nodes: GraphNode[], f: Filters): GraphNode[] {
  return nodes.filter((n) => (!f.category || n.category === f.category) && (!f.statuses.length || f.statuses.includes(n.status)))
}
export function searchMatches(nodes: GraphNode[], q: string): Set<string> | null {
  return norm(q) ? new Set(nodes.filter((n) => matchesQuery(n, q)).map((n) => n.skill_id)) : null
}
/** Best search hit: exact name, then name prefix, then shallowest/alphabetical, so Enter is predictable. */
export function bestMatch(nodes: GraphNode[], q: string): GraphNode | undefined {
  const t = norm(q); if (!t) return undefined
  return nodes.filter((n) => matchesQuery(n, q)).sort((a, b) => rank(a, t) - rank(b, t) || a.depth - b.depth || a.name.localeCompare(b.name))[0]
}
const rank = (n: GraphNode, t: string) => (n.name.toLowerCase() === t ? 0 : n.name.toLowerCase().startsWith(t) ? 1 : 2)

/** The skill plus its transitive prerequisites and dependents (bounded). Used for "related skills" focus and for large graphs. */
export function neighbourhood(ix: GraphIndex, id: string, max = 80): Set<string> {
  return new Set([id, ...reach(ix.up, id, max), ...reach(ix.down, id, max)])
}

export const MAX_GRAPH_NODES = 300
export interface Visible { nodes: GraphNode[]; edges: GraphEdge[]; truncated: number }
/** What is drawn. Focus mode shows only the selection's neighbourhood; very large results are capped (shallowest first) and reported. */
export function visibleSubgraph(nodes: GraphNode[], edges: GraphEdge[], ix: GraphIndex, f: Filters, focusId: string | null, cap = MAX_GRAPH_NODES): Visible {
  let list = applyFilters(nodes, f)
  if (focusId && ix.byId.has(focusId)) { const nb = neighbourhood(ix, focusId); list = list.filter((n) => nb.has(n.skill_id)) }
  let truncated = 0
  if (list.length > cap) {
    truncated = list.length - cap
    const keep = new Set(list.map((n) => n.skill_id)); const must = focusId && keep.has(focusId) ? neighbourhood(ix, focusId, cap) : new Set<string>()
    list = [...list].sort((a, b) => Number(must.has(b.skill_id)) - Number(must.has(a.skill_id)) || a.depth - b.depth || a.name.localeCompare(b.name)).slice(0, cap)
  }
  const ids = new Set(list.map((n) => n.skill_id))
  return { nodes: list, edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)), truncated }
}

/** Layout depends only on topology (which nodes, which edges), never on status or selection, so progress updates do not re-layout. */
export function topologyKey(ids: string[], edges: GraphEdge[]): string {
  return `${[...ids].sort().join(',')}|${edges.map((e) => `${e.source}>${e.target}`).sort().join(',')}`
}
