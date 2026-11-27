<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useOnline } from '@vueuse/core'
import { toast } from 'vue-sonner'
import { ArrowLeft, History, Play, RefreshCw, Send, Square, WifiOff } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import Tabs from '@/components/ui/Tabs.vue'
import Progress from '@/components/ui/Progress.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Dialog from '@/components/ui/Dialog.vue'
import SectionBody from '@/features/history/SectionBody.vue'
import { useSessionStream } from '@/features/history/useSessionStream'
import { SECTION_TABS, contentTypeLabel, isExtra, progressText, sectionStatus, sessionStatus } from '@/features/history/historyUi'
import { historyApi } from '@/services/api'
import { ApiError } from '@/services/http'
import { useContentHistoryStore } from '@/stores/contentHistory'
import { relativeTime } from '@/utils/format'

const route = useRoute(); const router = useRouter(); const qc = useQueryClient(); const pointer = useContentHistoryStore(); const online = useOnline()
const id = computed(() => route.params.id as string)
const stream = useSessionStream()

// Persisted state comes from the backend; nothing here triggers generation just because the page was opened.
const detail = useQuery({
  queryKey: computed(() => ['content-session', id.value]), queryFn: ({ signal }) => historyApi.get(id.value, { signal }), retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  // recover a generation that is still running on the server (e.g. after a refresh): poll its status until it settles
  refetchInterval: (q) => (!stream.running.value && q.state.data?.active_jobs?.length ? 3000 : false),
})
const content = useQuery({ queryKey: computed(() => ['content-session', id.value, 'content']), queryFn: ({ signal }) => historyApi.content(id.value, { signal }), retry: false,
  refetchInterval: () => (!stream.running.value && detail.data.value?.active_jobs?.length ? 3000 : false) })
const notFound = computed(() => detail.error.value instanceof ApiError && detail.error.value.status === 404)
watch(notFound, (v) => { if (v) pointer.forget(id.value) })
watch(() => detail.data.value, (d) => { if (d) pointer.remember(d.id, d.title) })

const live = reactive<Record<string, unknown>>({}) // sections received on the current stream, shown before the refetch lands
const sections = computed<Record<string, unknown>>(() => ({ ...(content.data.value?.sections ?? {}), ...live }))
const meta = (key: string) => detail.data.value?.sections.find((s) => s.section_key === key)
const statusOf = (key: string) => (key in live ? 'COMPLETED' : stream.generating.value.includes(key) ? 'GENERATING' : (meta(key)?.status ?? 'NOT_STARTED'))
type Core = { key: string; tab: string; label: string }
const planMeta = computed(() => (detail.data.value?.metadata as { plan?: { skill: string; source_assessment_id: string; recommended_concepts: string[]; overall_score: number } } | undefined)?.plan)
const core = computed<Core[]>(() => (planMeta.value ? (detail.data.value?.sections ?? []).filter((s) => !isExtra(s.section_key)).map((s) => ({ key: s.section_key, tab: s.section_key, label: s.section_title })) : SECTION_TABS))
const extras = computed(() => (detail.data.value?.sections ?? []).filter((s) => isExtra(s.section_key) && s.section_key in sections.value))
const tabs = computed(() => [...core.value.map((t) => ({ value: t.tab, label: t.label })), ...(extras.value.length ? [{ value: 'followups', label: `Follow-ups (${extras.value.length})` }] : [])])
const tab = ref('summary')
const keyOfTab = (t: string) => core.value.find((x) => x.tab === t)?.key ?? ''
watch(core, (c) => { if (c.length && !c.some((x) => x.tab === tab.value) && tab.value !== 'followups') tab.value = c[0].tab }, { immediate: true })

async function settle() {
  await Promise.all([qc.invalidateQueries({ queryKey: ['content-session', id.value] }), qc.invalidateQueries({ queryKey: ['content-sessions'] })])
  for (const k of Object.keys(live)) delete live[k]
}
async function run(path: string, body: unknown = {}, doneMsg?: string) {
  const ok = await stream.run(path, body, (e) => { if (e.ok) live[e.name] = e.value })
  await settle()
  if (ok && doneMsg) toast.success(doneMsg)
  if (stream.error.value) toast.error(stream.error.value)
}
const generate = (key: string) => run(historyApi.path.generate(id.value, key), {}, 'Section generated')
const regenerate = (key: string) => run(historyApi.path.regenerate(id.value, key), {}, 'New version saved — the previous one is kept')
const resume = () => run(historyApi.path.cont(id.value), {}, 'All sections are ready')
const missing = computed(() => core.value.filter((t) => statusOf(t.key) !== 'COMPLETED').length)

// "Continue learning" from the history list opens with ?resume=1: resume exactly once, only after the saved state has loaded.
const resumed = ref(false)
watch(() => detail.data.value, (d) => {
  if (d && route.query.resume && !resumed.value) {
    resumed.value = true; router.replace({ query: {} })
    if (d.sections.some((s) => !isExtra(s.section_key) && s.status !== 'COMPLETED') && !d.active_jobs.length) resume()
  }
}, { immediate: true })

const ask = ref('')
async function followUp() {
  const text = ask.value.trim(); if (text.length < 3) return
  await run(historyApi.path.cont(id.value), { instruction: text }, 'Added to this session'); ask.value = ''; tab.value = 'followups'
}

const versionsFor = ref<string | null>(null)
const versionsOpen = computed({ get: () => !!versionsFor.value, set: (v: boolean) => { if (!v) versionsFor.value = null } })
const versions = useQuery({ queryKey: computed(() => ['content-session', id.value, 'versions', versionsFor.value]), queryFn: ({ signal }) => historyApi.versions(id.value, versionsFor.value!, { signal }), enabled: computed(() => !!versionsFor.value) })
const activate = useMutation({ mutationFn: (v: number) => historyApi.activateVersion(id.value, versionsFor.value!, v), onSuccess: async () => { versionsFor.value = null; await settle(); toast.success('Version restored') }, onError: (e) => toast.error((e as Error).message) })
const d = computed(() => detail.data.value)
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-5">
    <RouterLink :to="{ name: 'history' }" class="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"><ArrowLeft class="size-4" aria-hidden="true" /> My Learning History</RouterLink>
    <div v-if="!online" role="status" class="flex items-center gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm"><WifiOff class="size-4" aria-hidden="true" />You are offline. Saved content stays readable; generation needs a connection.</div>

    <div v-if="detail.isPending.value" class="space-y-3" role="status" aria-label="Loading saved content"><Skeleton class="h-8 w-1/2" /><Skeleton class="h-4 w-1/3" /><Skeleton class="h-64" /></div>
    <EmptyState v-else-if="notFound" :icon="History" title="This content is no longer available" description="It may have been deleted. Your other saved content is in your history.">
      <Button @click="router.push({ name: 'history' })">Back to history</Button></EmptyState>
    <ErrorState v-else-if="detail.isError.value" :message="(detail.error.value as Error)?.message" @retry="detail.refetch()" />

    <template v-else-if="d">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0"><h1 class="truncate text-2xl font-semibold">{{ d.title }}</h1>
          <p class="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground"><span>{{ d.learning_level }} · {{ contentTypeLabel(d.content_type) }}</span>
            <Badge :tone="sessionStatus(d.status).tone">{{ sessionStatus(d.status).label }}</Badge><span>Updated {{ relativeTime(d.updated_at) }}</span></p></div>
        <div class="flex gap-2"><Button v-if="stream.running.value" variant="outline" @click="stream.stop()"><Square /> Stop</Button>
          <Button v-else-if="missing" :disabled="!online" @click="resume"><Play /> Continue learning</Button></div>
      </div>
      <Card v-if="planMeta" class="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"><span>Built from your <strong>{{ planMeta.skill }}</strong> assessment ({{ planMeta.overall_score }}%). Targets: {{ planMeta.recommended_concepts.join(', ') }}.</span>
        <RouterLink class="font-medium text-primary hover:underline" :to="{ name: 'assessment-run', params: { id: planMeta.source_assessment_id } }">View assessment results</RouterLink></Card>
      <div class="space-y-1"><Progress :value="d.completed_sections" :max="Math.max(1, d.total_sections)" label="Sections completed" /><p class="text-xs text-muted-foreground" role="status">{{ progressText(d.completed_sections, d.total_sections) }}</p></div>

      <div v-if="stream.running.value" role="status" class="flex items-center gap-2 rounded-md border bg-muted/40 p-3 text-sm"><RefreshCw class="size-4 animate-spin" aria-hidden="true" />
        {{ stream.generating.value.length ? `Generating ${stream.generating.value.length} section(s)…` : 'Working…' }} <span class="text-xs text-muted-foreground">{{ stream.detail.value }}</span></div>
      <div v-else-if="d.active_jobs.length" role="status" class="flex items-center gap-2 rounded-md border bg-muted/40 p-3 text-sm"><RefreshCw class="size-4 animate-spin" aria-hidden="true" />Generation is in progress for this session. This page updates automatically.</div>
      <div v-if="stream.error.value && !stream.running.value" role="alert" class="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm"><span class="flex-1">{{ stream.error.value }}</span><Button size="sm" variant="outline" :disabled="!online" @click="resume"><RefreshCw /> Resume</Button></div>

      <ul class="flex flex-wrap gap-1.5" aria-label="Section status">
        <li v-for="t in core" :key="t.key"><button type="button" class="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-accent" :aria-label="`${t.label}: ${sectionStatus(statusOf(t.key) as any).label}`" @click="tab = t.tab">
          {{ t.label }} <Badge :tone="sectionStatus(statusOf(t.key) as any).tone">{{ sectionStatus(statusOf(t.key) as any).label }}</Badge></button></li>
      </ul>

      <div v-if="content.isPending.value" class="space-y-2" role="status"><Skeleton class="h-4 w-3/4" /><Skeleton class="h-4 w-full" /><Skeleton class="h-4 w-5/6" /></div>
      <ErrorState v-else-if="content.isError.value && !Object.keys(sections).length" message="The saved content could not be loaded." @retry="content.refetch()" />
      <Tabs v-else v-model="tab" label="Saved study material" :tabs="tabs">
        <template v-for="t in tabs" :key="t.value" #[t.value]>
          <div v-if="t.value === 'followups'" class="space-y-4"><Card v-for="x in extras" :key="x.section_key" class="p-4"><h3 class="mb-2 text-sm font-semibold">{{ x.section_title }}</h3><SectionBody :section-key="x.section_key" :value="sections[x.section_key]" /></Card></div>
          <template v-else>
            <template v-if="keyOfTab(t.value) in sections">
              <div class="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>Version {{ meta(keyOfTab(t.value))?.content_version ?? 1 }}</span>
                <Button size="sm" variant="outline" :disabled="stream.running.value || !online" @click="regenerate(keyOfTab(t.value))"><RefreshCw /> Regenerate</Button>
                <Button size="sm" variant="ghost" @click="versionsFor = keyOfTab(t.value)">Versions</Button></div>
              <SectionBody :section-key="keyOfTab(t.value)" :value="sections[keyOfTab(t.value)]" />
            </template>
            <div v-else-if="statusOf(keyOfTab(t.value)) === 'GENERATING'" class="space-y-2" role="status"><p class="text-xs text-muted-foreground">Writing this section…</p><Skeleton class="h-4 w-3/4" /><Skeleton class="h-4 w-full" /></div>
            <EmptyState v-else :title="statusOf(keyOfTab(t.value)) === 'FAILED' ? 'This section could not be generated' : 'This section has not been generated yet'" description="Generate it now; your other sections are not affected.">
              <Button :disabled="stream.running.value || !online" @click="generate(keyOfTab(t.value))"><Play /> Generate this section</Button></EmptyState>
          </template>
        </template>
      </Tabs>

      <Card class="p-4"><form class="flex flex-wrap gap-2" @submit.prevent="followUp"><Input v-model="ask" aria-label="Ask a follow-up" maxlength="500" class="flex-1" :disabled="stream.running.value || !online" placeholder="Ask for more: “Explain idempotency with a practical example…”" />
        <Button type="submit" :disabled="ask.trim().length < 3 || stream.running.value || !online"><Send /> Add to this session</Button></form>
        <p class="mt-2 text-xs text-muted-foreground">New content is added alongside your existing sections; nothing already saved is overwritten.</p></Card>
      <p class="text-xs text-muted-foreground">AI-generated material can contain errors — verify important facts against official documentation.</p>
    </template>

    <Dialog v-model:open="versionsOpen" title="Section versions" description="Earlier versions are kept. Restoring one makes it the active version.">
      <div v-if="versions.isPending.value" role="status"><Skeleton class="h-10" /></div>
      <ul v-else class="divide-y text-sm"><li v-for="v in versions.data.value?.items ?? []" :key="v.version" class="flex items-center justify-between py-2">
        <span>Version {{ v.version }} <Badge v-if="v.active" tone="success">Active</Badge><span class="ml-2 text-xs text-muted-foreground">{{ relativeTime(v.created_at) }}</span></span>
        <Button v-if="!v.active" size="sm" variant="outline" :loading="activate.isPending.value" @click="activate.mutate(v.version)">Restore</Button></li></ul>
    </Dialog>
  </div>
</template>
