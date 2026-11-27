/** Provisional, still-being-written section content built from `chunk` SSE events.
 *  A `chunk` is never authoritative: the validated `section` event that follows always replaces it. */
export interface PartialSection { attempt: number; seq: number; text: string; items: unknown[] }
export interface ChunkEvent { name: string; attempt: number; seq: number; kind: 'md' | 'item'; delta?: string; item?: unknown }

/** Pure reducer: a new attempt (a retry/repair) starts from scratch, stale or duplicate chunks are ignored. */
export function applyChunk(cur: PartialSection | undefined, c: ChunkEvent): PartialSection | undefined {
  if (cur && c.attempt < cur.attempt) return cur
  const base: PartialSection = !cur || c.attempt > cur.attempt ? { attempt: c.attempt, seq: -1, text: '', items: [] } : cur
  if (c.seq <= base.seq) return base
  return {
    attempt: base.attempt, seq: c.seq,
    text: c.kind === 'md' ? base.text + (c.delta ?? '') : base.text,
    items: c.kind === 'item' && c.item !== undefined ? [...base.items, c.item] : base.items,
  }
}

/** Section icons arrive from the backend as Lucide tokens (never model-generated); unknown tokens fall back to a document. */
export const FALLBACK_ICON = 'file-text'
