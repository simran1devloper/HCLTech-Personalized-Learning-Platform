import type { Milestone, Roadmap } from '@/types/api'

export interface VersionDiff {
  added: Milestone[]; removed: Milestone[]
  statusChanged: { skill: string; from: string; to: string }[]
  hoursDelta: number
}
const live = (r: Roadmap) => r.milestones.filter((m) => m.status !== 'superseded')

/** Presentational comparison of two stored versions (what is in each), not a recomputation of priorities. */
export function compareVersions(a: Roadmap, b: Roadmap): VersionDiff {
  const key = (m: Milestone) => `${m.skill_id}:${m.kind}`
  const am = new Map(live(a).map((m) => [key(m), m])), bm = new Map(live(b).map((m) => [key(m), m]))
  return {
    added: [...bm].filter(([k]) => !am.has(k)).map(([, m]) => m),
    removed: [...am].filter(([k]) => !bm.has(k)).map(([, m]) => m),
    statusChanged: [...bm].filter(([k, m]) => am.has(k) && am.get(k)!.status !== m.status).map(([k, m]) => ({ skill: m.skill_name, from: am.get(k)!.status, to: m.status })),
    hoursDelta: +(b.total_hours - a.total_hours).toFixed(1),
  }
}

/** Groups milestones into estimated N-week blocks from backend-provided hours (scheduling view only). */
export function weekBlocks(r: Roadmap, blockWeeks = 4): { label: string; items: Milestone[] }[] {
  const per = Math.max(0.5, r.hours_per_week) * blockWeeks
  const blocks = new Map<number, Milestone[]>()
  let acc = 0
  for (const m of live(r).sort((x, y) => x.order - y.order)) {
    const idx = Math.floor(acc / per)
    blocks.set(idx, [...(blocks.get(idx) ?? []), m])
    if (m.status !== 'completed' && m.status !== 'skipped_mastered') acc += m.estimated_hours
  }
  return [...blocks].sort((x, y) => x[0] - y[0]).map(([i, items]) => ({ label: `Weeks ${i * blockWeeks + 1}–${(i + 1) * blockWeeks}`, items }))
}
