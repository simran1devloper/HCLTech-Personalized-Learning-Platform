/** Deterministic reading helpers: no model call, no invented content. Everything is computed from the generated sections themselves. */
import { toDisplayText } from '@/utils/display'

const WPM = 200
export const wordCount = (text: string) => (text.trim() ? text.trim().split(/\s+/).length : 0)

export function textOf(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(textOf).join(' ')
  if (typeof value === 'object') return Object.values(value as Record<string, unknown>).map(textOf).join(' ')
  return String(value)
}
/** Minutes to read a section (at least 1 when it has any content). */
export function readMinutes(value: unknown): number {
  const w = wordCount(textOf(value))
  return w ? Math.max(1, Math.round(w / WPM)) : 0
}

export interface Concept { term: string; definition: string }
export const conceptsOf = (value: unknown): Concept[] =>
  Array.isArray(value) ? (value as Concept[]).filter((c) => c && c.term).map((c) => ({ term: toDisplayText(c.term), definition: toDisplayText(c.definition) })) : []

/** Learning objectives straight from the extracted key concepts ("Understand <term>"), capped. Empty when no concepts exist yet. */
export function objectivesFrom(concepts: Concept[], max = 6): string[] {
  const seen = new Set<string>(); const out: string[] = []
  for (const c of concepts) { const k = c.term.trim(); if (k && !seen.has(k.toLowerCase())) { seen.add(k.toLowerCase()); out.push(`Understand ${k}`) } if (out.length >= max) break }
  return out
}

/** Key takeaways: the summary's own bullet points if it has any, otherwise its first sentences. Plain text, max 4. */
export function takeawaysFrom(markdown: string, max = 4): string[] {
  const clean = (s: string) => s.replace(/\*\*|__|`/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/\s+/g, ' ').trim()
  const bullets = [...markdown.matchAll(/^\s*(?:[-*]|\d+\.)\s+(.+)$/gm)].map((m) => clean(m[1])).filter((s) => s.length > 15)
  if (bullets.length) return bullets.slice(0, max)
  const prose = markdown.split('\n').filter((l) => l.trim() && !l.startsWith('#') && !l.startsWith('>') && !l.startsWith('```')).join(' ')
  return (clean(prose).match(/[^.!?]+[.!?]/g) ?? []).map((s) => s.trim()).filter((s) => s.length > 25).slice(0, Math.min(max, 3))
}

export interface Progress { total: number; understood: number; opened: number; understoodPct: number; readingPct: number }
export function progressOf(keys: string[], state: Record<string, { understood?: boolean; opened_at?: string | null }>): Progress {
  const total = keys.length
  const understood = keys.filter((k) => state[k]?.understood).length
  const opened = keys.filter((k) => state[k]?.opened_at).length
  return { total, understood, opened, understoodPct: total ? Math.round((100 * understood) / total) : 0, readingPct: total ? Math.round((100 * opened) / total) : 0 }
}
