import { computed, reactive, ref, watch, type Ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { historyApi, workspaceApi } from '@/services/api'
import { ApiError } from '@/services/http'
import { useContentStream } from '@/features/study/useContentStream'
import { titleOf, WORKSPACE_TITLES } from './titles'
import { monitorRows, summarizeRows } from './monitor'
import { progressOf } from './derive'
import type { SectionState, StudyProfile } from '@/types/api'

export interface StartParams { topic: string; level: string; score: number; diag?: string }

/** The study workspace state machine. Server state (session, content, reading state, pipeline report) comes from TanStack Query and survives a refresh;
 *  the SSE stream only adds the sections still being written. Completed sections are never regenerated: every run asks the server for the missing ones. */
export function useWorkspace(sessionId: Ref<string | undefined>, start: StartParams | null) {
  const qc = useQueryClient()
  const stream = useContentStream()
  const id = ref<string | undefined>(sessionId.value)
  watch(sessionId, (v) => { if (v) id.value = v })

  const detail = useQuery({ queryKey: computed(() => ['content-session', id.value]), queryFn: ({ signal }) => historyApi.get(id.value!, { signal }), enabled: computed(() => !!id.value),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2 })
  const content = useQuery({ queryKey: computed(() => ['content-session', id.value, 'content']), queryFn: ({ signal }) => historyApi.content(id.value!, { signal }), enabled: computed(() => !!id.value), retry: false })
  const stateQ = useQuery({ queryKey: computed(() => ['workspace-state', id.value]), queryFn: ({ signal }) => workspaceApi.state(id.value!, { signal }), enabled: computed(() => !!id.value) })
  const report = useQuery({ queryKey: computed(() => ['workspace-pipeline', id.value]), queryFn: ({ signal }) => workspaceApi.pipeline(id.value!, { signal }), enabled: computed(() => !!id.value),
    refetchInterval: () => (!stream.running.value && detail.data.value?.active_jobs?.length ? 3000 : false) })   // another tab / device is generating: keep the monitor honest

  const live = reactive<Record<string, unknown>>({})
  const sections = computed<Record<string, unknown>>(() => ({ ...(content.data.value?.sections ?? {}), ...live, ...Object.fromEntries(Object.entries(stream.sections).filter(([k]) => !stream.failed.value.includes(k))) }))
  // Provisional text/items of sections that are still being written (from `chunk` events). Never authoritative: a completed section always wins and
  // replaces the preview, and previews never count as "content" for navigation, reading time or knowledge checks.
  const previews = computed<Record<string, unknown>>(() => {
    const out: Record<string, unknown> = {}
    for (const [k, p] of Object.entries(stream.partial)) {
      if (k in sections.value) continue
      if (p.items.length) out[k] = p.items
      else if (p.text.trim()) out[k] = p.text
    }
    return out
  })
  const core = computed(() => (detail.data.value?.sections ?? Object.keys(WORKSPACE_TITLES).map((k) => ({ section_key: k, section_title: titleOf(k), status: 'NOT_STARTED' }))).filter((s) => !s.section_key.startsWith('extra-'))
    .map((s) => ({ key: s.section_key, title: titleOf(s.section_key, s.section_title) })))
  const rows = computed(() => monitorRows(core.value, stream.pipeline.stages.value, report.data.value?.sections, stream.running.value))
  const summary = computed(() => summarizeRows(rows.value))
  const profile = computed<StudyProfile | null>(() => ((detail.data.value?.context as { profile?: StudyProfile } | undefined)?.profile) ?? null)
  const reading = computed(() => progressOf(core.value.map((c) => c.key), stateQ.data.value?.sections ?? {}))

  // ---- generation (always for the missing sections only)
  const request = computed(() => {
    const m = (detail.data.value?.metadata as { request?: { topic: string; level: string; score: number } } | undefined)?.request
    const base = m ?? (start ? { topic: start.topic, level: start.level, score: start.score } : null)
    return base && { ...base, diagnostic_session_id: profile.value?.diagnostic_session_id ?? start?.diag ?? undefined }
  })
  async function run(only?: string[]) {
    const body = request.value; if (!body) return false
    const ok = await stream.run({ ...body, ...(id.value ? { session_id: id.value } : {}) }, only)
    if (stream.sessionId.value) id.value = stream.sessionId.value
    await Promise.all(['content-session', 'workspace-state', 'workspace-pipeline', 'content-sessions'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
    for (const k of Object.keys(live)) delete live[k]
    if (stream.error.value) toast.error(stream.error.value)
    return ok
  }
  const generateMissing = () => run(summary.value.missing.length && summary.value.missing.length < core.value.length ? summary.value.missing : undefined)
  const retry = (key: string) => run([key])
  const cancel = () => { stream.stop(); toast.info('Stopped. Finished sections are saved; you can continue any time.') }

  // ---- reading state (optimistic, persisted)
  const blank: SectionState = { understood: false, bookmarked: false, note: '', opened_at: null }
  const mutate = useMutation({
    mutationFn: ({ key, patch }: { key: string; patch: { understood?: boolean; bookmarked?: boolean; note?: string; opened?: boolean } }) => workspaceApi.setState(id.value!, key, patch),
    onMutate: async ({ key, patch }) => {
      await qc.cancelQueries({ queryKey: ['workspace-state', id.value] })
      const prev = qc.getQueryData<{ sections: Record<string, SectionState>; progress: unknown }>(['workspace-state', id.value])
      if (prev) qc.setQueryData(['workspace-state', id.value], { ...prev, sections: { ...prev.sections, [key]: { ...blank, ...prev.sections[key], ...(patch.understood !== undefined && { understood: patch.understood }), ...(patch.bookmarked !== undefined && { bookmarked: patch.bookmarked }), ...(patch.note !== undefined && { note: patch.note }), ...(patch.opened && { opened_at: new Date().toISOString() }) } } })
      return { prev }
    },
    onError: (e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(['workspace-state', id.value], ctx.prev); toast.error((e as Error).message) },
    onSettled: () => qc.invalidateQueries({ queryKey: ['workspace-state', id.value] }),
  })
  const setState = (key: string, patch: { understood?: boolean; bookmarked?: boolean; note?: string; opened?: boolean }) => { if (id.value) mutate.mutate({ key, patch }) }
  const stateOf = (key: string): SectionState => stateQ.data.value?.sections[key] ?? { understood: false, bookmarked: false, note: '', opened_at: null }

  const notFound = computed(() => detail.error.value instanceof ApiError && detail.error.value.status === 404)
  return { id, stream, detail, content, report, sections, previews, core, rows, summary, profile, reading, request, run, generateMissing, retry, cancel, setState, stateOf, notFound, stateQ }
}
