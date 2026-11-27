export const pct = (v: number | null | undefined, digits = 0) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)
export const level = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(1))
export function dateShort(iso: string): string {
  const d = new Date(iso)
  return isNaN(+d) ? iso : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
export function relativeTime(iso: string, now = Date.now()): string {
  const s = Math.round((now - +new Date(iso)) / 1000)
  if (isNaN(s)) return iso
  const units: [number, string][] = [[86400, 'day'], [3600, 'hour'], [60, 'minute']]
  for (const [secs, name] of units) if (Math.abs(s) >= secs) { const n = Math.floor(Math.abs(s) / secs); return `${n} ${name}${n > 1 ? 's' : ''} ago` }
  return 'just now'
}
