export type StageStatus = 'pending' | 'running' | 'retrying' | 'done' | 'failed' | 'skipped'
export interface PipelineStage {
  id: string; label: string; status: StageStatus
  attempt?: number; max_attempts?: number; duration_ms?: number; detail?: string
  icon?: string // Lucide token chosen by the backend registry, e.g. 'key-round'
}
export type PipelineState = 'idle' | 'running' | 'completed' | 'attention' | 'cancelled'

const FINAL: StageStatus[] = ['done', 'failed', 'skipped']

/** Pure reducer: applies one backend stage event. Unknown stages are appended; a finished stage is never
 *  reverted to running by a late/duplicate event (keeps the checklist monotonic). */
export function applyStage(stages: PipelineStage[], ev: PipelineStage): PipelineStage[] {
  const i = stages.findIndex((s) => s.id === ev.id)
  if (i === -1) return [...stages, ev]
  const cur = stages[i]
  if (FINAL.includes(cur.status) && !FINAL.includes(ev.status) && ev.status !== 'retrying') return stages
  const next = stages.slice(); next[i] = { ...cur, ...ev }
  return next
}

export function summarize(stages: PipelineStage[]) {
  const done = stages.filter((s) => s.status === 'done' || s.status === 'skipped').length
  const failed = stages.filter((s) => s.status === 'failed').length
  const active = stages.filter((s) => s.status === 'running' || s.status === 'retrying')
  return { total: stages.length, done, failed, active, finished: done + failed }
}

export function stateOf(running: boolean, stages: PipelineStage[], cancelled: boolean): PipelineState {
  if (running) return 'running'
  if (cancelled) return 'cancelled'
  if (!stages.length) return 'idle'
  const { failed, finished, total } = summarize(stages)
  return failed || finished < total ? 'attention' : 'completed'
}

export const formatMs = (ms?: number) => (ms == null ? '' : ms < 1000 ? `${ms} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`)
