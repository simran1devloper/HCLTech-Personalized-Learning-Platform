import type { PipelineStage } from '@/features/pipeline/pipeline'

export type TaskStatus = 'queued' | 'running' | 'completed' | 'reused' | 'failed' | 'not_generated'
export interface ServerRow { section_key: string; title: string; status: string; source: 'generated' | 'reused' | null; input_tokens: number; output_tokens: number; latency_ms: number; attempts: number; error: string | null }
export interface TaskRow { key: string; title: string; status: TaskStatus; durationMs?: number; tokens?: number; attempts?: number; detail?: string; error?: string | null }

const REUSE_HINT = /saved earlier|shared saved copy|saved copy/i

/** One monitor row per section: live SSE stage events win while a run is active; otherwise the persisted server report (so a refresh shows the same truth).
 *  'reused' means the section came from storage and cost no model call. */
export function monitorRows(core: { key: string; title: string }[], live: PipelineStage[], server: ServerRow[] | undefined, running: boolean): TaskRow[] {
  const liveBy = new Map(live.map((s) => [s.id.replace(/^section:/, ''), s]))
  const serverBy = new Map((server ?? []).map((r) => [r.section_key, r]))
  return core.map(({ key, title }) => {
    const l = liveBy.get(key); const sv = serverBy.get(key)
    const tokens = sv ? sv.input_tokens + sv.output_tokens : undefined
    if (l && (running || !sv)) {
      const status: TaskStatus = l.status === 'done' ? (REUSE_HINT.test(l.detail ?? '') ? 'reused' : 'completed') : l.status === 'failed' ? 'failed' : l.status === 'running' || l.status === 'retrying' ? 'running' : l.status === 'skipped' ? 'not_generated' : 'queued'
      return { key, title, status, durationMs: l.duration_ms, attempts: l.attempt, detail: l.detail, tokens, error: status === 'failed' ? l.detail ?? 'Failed' : null }
    }
    if (sv) {
      const status: TaskStatus = sv.status === 'COMPLETED' ? (sv.source === 'reused' ? 'reused' : 'completed') : sv.status === 'FAILED' ? 'failed' : sv.status === 'GENERATING' || sv.status === 'QUEUED' ? 'running' : running ? 'queued' : 'not_generated'
      return { key, title, status, durationMs: sv.latency_ms || undefined, tokens: sv.input_tokens + sv.output_tokens || undefined, attempts: sv.attempts || undefined, error: sv.error }
    }
    return { key, title, status: running ? 'queued' : 'not_generated' }
  })
}

export function summarizeRows(rows: TaskRow[]) {
  const finished = rows.filter((r) => r.status === 'completed' || r.status === 'reused').length
  return { total: rows.length, finished, failed: rows.filter((r) => r.status === 'failed').length, running: rows.filter((r) => r.status === 'running').length,
    reused: rows.filter((r) => r.status === 'reused').length, missing: rows.filter((r) => r.status === 'not_generated' || r.status === 'failed').map((r) => r.key),
    pct: rows.length ? Math.round((100 * finished) / rows.length) : 0 }
}

/** Remaining-time hint from what this run has actually done so far (never a made-up number): average duration of finished sections x sections left. */
export function etaSeconds(rows: TaskRow[]): number | null {
  const done = rows.filter((r) => r.status === 'completed' && r.durationMs)
  const left = rows.filter((r) => r.status === 'queued' || r.status === 'running').length
  if (!done.length || !left) return null
  return Math.round((done.reduce((a, r) => a + (r.durationMs ?? 0), 0) / done.length / 1000) * left)
}
