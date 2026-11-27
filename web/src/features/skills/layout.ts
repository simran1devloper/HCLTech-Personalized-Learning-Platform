import type { GraphEdge } from '@/types/api'
import { topologyKey } from './graph'

export const NODE_W = 208, NODE_H = 68
export type Positions = Map<string, { x: number; y: number }>
export interface LayoutInput { ids: string[]; edges: GraphEdge[] }

/** ELK is ~1.5 MB: it is loaded the first time a graph is laid out, never in the entry bundle. In a browser it runs in a Web Worker
 *  (the main thread stays responsive for large graphs); where Workers are unavailable (tests, old runtimes) it runs in-thread. */
type Elk = { layout: (g: unknown) => Promise<{ children?: { id: string; x?: number; y?: number }[] }> }
let elkPromise: Promise<Elk> | null = null
function getElk(): Promise<Elk> {
  elkPromise ??= (async () => {
    if (typeof Worker !== 'undefined') {
      try {
        const [{ default: ELK }, { default: ElkWorker }] = await Promise.all([import('elkjs/lib/elk-api.js'), import('elkjs/lib/elk-worker.min.js?worker')])
        return new ELK({ workerFactory: () => new ElkWorker() }) as unknown as Elk
      } catch { /* fall through to the in-thread build */ }
    }
    const { default: ELK } = await import('elkjs/lib/elk.bundled.js')
    return new ELK() as unknown as Elk
  })()
  return elkPromise
}

const OPTIONS = {
  'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.layered.layering.strategy': 'LONGEST_PATH',
  'elk.layered.spacing.nodeNodeBetweenLayers': '72', 'elk.spacing.nodeNode': '24', 'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF', 'elk.spacing.edgeNode': '20', 'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
}

/** Deterministic longest-path fallback used if ELK cannot load or throws, so the graph is never blank. O(V+E). */
export function fallbackLayout({ ids, edges }: LayoutInput, dx = NODE_W + 90, dy = NODE_H + 22): Positions {
  const set = new Set(ids); const pre = new Map<string, string[]>()
  for (const e of edges) if (set.has(e.source) && set.has(e.target)) (pre.get(e.target) ?? pre.set(e.target, []).get(e.target)!).push(e.source)
  const level = new Map<string, number>(); const visiting = new Set<string>()
  const depth = (n: string): number => {
    const c = level.get(n); if (c !== undefined) return c
    if (visiting.has(n)) return 0
    visiting.add(n); const d = Math.max(-1, ...(pre.get(n) ?? []).map(depth)) + 1; visiting.delete(n); level.set(n, d); return d
  }
  const rows = new Map<number, number>(); const out: Positions = new Map()
  for (const id of [...ids].sort()) { const l = depth(id), r = rows.get(l) ?? 0; rows.set(l, r + 1); out.set(id, { x: l * dx, y: r * dy }) }
  return out
}

const cache = new Map<string, Positions>() // small LRU: switching back to a career / filter already seen is instant
const CACHE_MAX = 12
export const layoutCacheSize = () => cache.size
export function clearLayoutCache() { cache.clear() }

export async function computeLayout(input: LayoutInput): Promise<Positions> {
  const key = topologyKey(input.ids, input.edges)
  const hit = cache.get(key)
  if (hit) { cache.delete(key); cache.set(key, hit); return hit }
  let pos: Positions
  if (!input.ids.length) pos = new Map()
  else {
    try {
      const set = new Set(input.ids)
      const g = await (await getElk()).layout({
        id: 'root', layoutOptions: OPTIONS,
        children: [...input.ids].sort().map((id) => ({ id, width: NODE_W, height: NODE_H })),
        edges: input.edges.filter((e) => set.has(e.source) && set.has(e.target)).map((e) => ({ id: `${e.source}>${e.target}`, sources: [e.source], targets: [e.target] })),
      })
      pos = new Map((g.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]))
      if (pos.size !== input.ids.length) throw new Error('incomplete layout')
    } catch { pos = fallbackLayout(input) }
  }
  cache.set(key, pos)
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value as string)
  return pos
}
