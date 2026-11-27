/** Turns any value into text a person can read. Never returns "[object Object]" or a Python/JSON container dump. */
export function toDisplayText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return tryParseRepr(v) ?? v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return v.map(toDisplayText).filter(Boolean).join(', ')
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    for (const k of ['title', 'name', 'term', 'area', 'topic', 'text', 'description']) {
      if (typeof o[k] === 'string' && (o[k] as string).trim()) {
        const rest = Object.entries(o).filter(([kk]) => kk !== k).map(([kk, vv]) => `${kk}: ${toDisplayText(vv)}`).filter((s) => !s.endsWith(': '))
        return (o[k] as string).trim() + (rest.length ? ` — ${rest.join('; ')}` : '')
      }
    }
    return Object.entries(o).map(([k, x]) => `${k}: ${toDisplayText(x)}`).filter((s) => !s.endsWith(': ')).join('; ')
  }
  return String(v)
}

/** Strings that are really a Python/JSON dict or list dump (e.g. "{'a': 1}") are parsed and re-rendered readably. */
function tryParseRepr(s: string): string | null {
  const t = s.trim()
  if (!t || !'{['.includes(t[0]) || !'}]'.includes(t.at(-1)!)) return null
  const candidates = [t, t.replace(/\bTrue\b/g, 'true').replace(/\bFalse\b/g, 'false').replace(/\bNone\b/g, 'null').replace(/'/g, '"')]
  for (const c of candidates) {
    try { const parsed = JSON.parse(c); if (parsed && typeof parsed === 'object') return toDisplayText(parsed) } catch { /* try next */ }
  }
  return null
}

/** True if the text contains something that looks like a leaked object (used by tests and the render guard). */
export const looksLeaked = (s: string) => /\[object Object\]|^\s*\{'|\bNone\b,|<class /.test(s)
