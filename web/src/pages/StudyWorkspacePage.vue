<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, ArrowLeft, ArrowRight, Bookmark, CheckCircle2, Clock, History, Layers, Play, RefreshCw } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import PipelineMonitor from '@/features/workspace/PipelineMonitor.vue'
import WorkspaceHeader from '@/features/workspace/WorkspaceHeader.vue'
import SectionNav from '@/features/workspace/SectionNav.vue'
import SectionRail from '@/features/workspace/SectionRail.vue'
import RichSection from '@/features/workspace/RichSection.vue'
import KnowledgeCheck from '@/features/workspace/KnowledgeCheck.vue'
import NotesBox from '@/features/workspace/NotesBox.vue'
import { useWorkspace } from '@/features/workspace/useWorkspace'
import { conceptsOf, readMinutes } from '@/features/workspace/derive'

/** AI learning workspace for generated study material. `/study/learn?topic=…` starts a generation (and moves to `/study/learn/:id`); `/study/learn/:id` restores a
 *  saved session after a refresh and only ever generates what is missing, and only when the learner asks. */
const route = useRoute(); const router = useRouter()
const routeId = computed(() => (typeof route.params.id === 'string' ? route.params.id : undefined))
const q = route.query
const start = !routeId.value && typeof q.topic === 'string' && typeof q.level === 'string'
  ? { topic: q.topic.slice(0, 80), level: q.level, score: Math.max(0, Math.min(10, Number(q.score) || 0)), diag: typeof q.diag === 'string' ? q.diag : undefined } : null
const ws = useWorkspace(routeId, start)
const current = ref(typeof q.s === 'string' ? q.s : '')
const topic = computed(() => ws.detail.data.value?.topic ?? start?.topic ?? '')
const level = computed(() => ws.request.value?.level ?? ws.detail.data.value?.learning_level ?? start?.level ?? '')
const started = ref(false)

onMounted(async () => {
  if (start && !started.value) { started.value = true; await ws.run() }
})
watch(() => ws.stream.sessionId.value, (sid) => { if (sid && !routeId.value) router.replace({ name: 'study-learn', params: { id: sid }, query: { s: current.value || undefined } }) })
watch(() => ws.core.value, (c) => { if (c.length && !c.some((x) => x.key === current.value)) current.value = c[0].key }, { immediate: true })
watch(current, (k) => { if (k && routeId.value) router.replace({ query: { ...route.query, s: k } }) })

const hasContent = (k: string) => k in ws.sections.value
// A section that is still being written shows its provisional text as it arrives; the validated section replaces it. Only finished sections count as content.
const previewing = computed(() => !hasContent(current.value) && current.value in ws.previews.value)
const shownValue = computed(() => (hasContent(current.value) ? ws.sections.value[current.value] : ws.previews.value[current.value]))
const row = computed(() => ws.rows.value.find((r) => r.key === current.value))
const idx = computed(() => ws.core.value.findIndex((c) => c.key === current.value))
const prev = computed(() => ws.core.value[idx.value - 1] ?? null)
const nextSec = computed(() => ws.core.value[idx.value + 1] ?? null)
const recommended = computed(() => ws.core.value.find((c, i) => i > idx.value && !ws.stateOf(c.key).understood && hasContent(c.key)) ?? ws.core.value.find((c) => !ws.stateOf(c.key).understood && c.key !== current.value && hasContent(c.key)) ?? null)
const st = computed(() => ws.stateOf(current.value))
const minutes = computed(() => readMinutes(ws.sections.value[current.value]))
const conceptCount = computed(() => conceptsOf(ws.sections.value.key_concepts).length)
const states = computed(() => ws.stateQ.data.value?.sections ?? {})

// reading progress: opening a section that has content marks it as read (once)
watch([current, () => hasContent(current.value), () => ws.id.value], ([k, has, id]) => { if (k && has && id && !ws.stateOf(k).opened_at) ws.setState(k, { opened: true }) }, { immediate: true })
function onKey(e: KeyboardEvent) {
  if (!e.altKey || (e.target as HTMLElement)?.matches?.('input,textarea,select')) return
  if (e.key === 'ArrowRight' && nextSec.value) { e.preventDefault(); current.value = nextSec.value.key }
  if (e.key === 'ArrowLeft' && prev.value) { e.preventDefault(); current.value = prev.value.key }
}
onMounted(() => window.addEventListener('keydown', onKey)); onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
const askTutor = () => router.push({ name: 'tutor', query: { skill: topic.value, ask: `Help me understand “${row.value?.title ?? ''}” in ${topic.value}. What should I focus on?` } })
const tokens = computed(() => (ws.report.data.value ? { input: ws.report.data.value.totals.input_tokens, output: ws.report.data.value.totals.output_tokens } : undefined))
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2"><Button size="icon" variant="ghost" aria-label="Back to Diagnostic Study" @click="router.push({ name: 'study' })"><ArrowLeft /></Button>
        <div><h1 class="text-xl font-semibold leading-tight">Diagnostic Study</h1><p class="text-xs text-muted-foreground">Your personalized learning workspace. Master the skills that matter for your career.</p></div></div>
      <Button v-if="ws.id.value" size="sm" variant="outline" @click="router.push({ name: 'history-session', params: { id: ws.id.value } })"><History /> Content History</Button>
    </div>

    <ErrorState v-if="ws.notFound.value" message="This study session no longer exists." :retryable="false" />
    <template v-else>
      <Skeleton v-if="!topic" class="h-24" role="status" aria-label="Loading study session" />
      <WorkspaceHeader v-else :topic="topic" :level="level" :profile="ws.profile.value" :progress="ws.reading.value" />

      <PipelineMonitor :rows="ws.rows.value" :running="ws.stream.running.value" :topic="topic" :tokens="tokens" :error="ws.stream.error.value"
        @cancel="ws.cancel()" @retry="ws.retry($event)" @continue="ws.generateMissing()" />

      <div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div class="min-w-0 space-y-3">
          <SectionNav :rows="ws.rows.value" :current="current" :states="states" @select="current = $event" />
          <Card :id="`panel-${current}`" role="tabpanel" :aria-labelledby="`tab-${current}`" tabindex="0" class="space-y-4 p-5">
            <template v-if="row">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0"><h2 class="text-xl font-semibold" data-test="section-title">{{ row.title }}</h2>
                  <p class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"><span v-if="minutes" class="inline-flex items-center gap-1"><Clock class="size-3.5" aria-hidden="true" />{{ minutes }} min read</span>
                    <span v-if="conceptCount" class="inline-flex items-center gap-1"><Layers class="size-3.5" aria-hidden="true" />{{ conceptCount }} key concepts</span><Badge v-if="level">{{ level }}</Badge></p></div>
                <div v-if="hasContent(current)" class="flex items-center gap-2">
                  <Button size="sm" variant="outline" :aria-pressed="st.bookmarked" @click="ws.setState(current, { bookmarked: !st.bookmarked })"><Bookmark :class="st.bookmarked && 'fill-current'" /> {{ st.bookmarked ? 'Bookmarked' : 'Bookmark' }}</Button>
                  <Button size="sm" :variant="st.understood ? 'secondary' : 'default'" :aria-pressed="st.understood" @click="ws.setState(current, { understood: !st.understood })"><CheckCircle2 /> {{ st.understood ? 'Understood' : 'Mark as Understood' }}</Button></div>
              </div>

              <p v-if="previewing" class="text-xs text-muted-foreground" role="status" data-testid="section-writing">Writing this section now… the text below is a live preview and is checked when it finishes.</p>
              <RichSection v-if="hasContent(current) || previewing" :section-key="current" :value="shownValue" :concepts="ws.sections.value.key_concepts" :streaming="previewing || (ws.stream.running.value && row.status === 'running')" />
              <div v-else-if="row.status === 'running' || row.status === 'queued'" class="space-y-2" role="status"><p class="text-sm text-muted-foreground">{{ row.status === 'running' ? 'Writing this section now…' : 'Queued. It will appear here as soon as it is written.' }} You can read finished sections meanwhile.</p><Skeleton class="h-4 w-3/4" /><Skeleton class="h-4 w-full" /><Skeleton class="h-4 w-5/6" /></div>
              <EmptyState v-else :icon="row.status === 'failed' ? AlertTriangle : Play" :title="row.status === 'failed' ? 'This section could not be generated' : 'This section has not been generated yet'" :description="row.error ?? 'Nothing else is regenerated.'">
                <Button :disabled="ws.stream.running.value" @click="ws.retry(current)"><RefreshCw /> {{ row.status === 'failed' ? 'Retry this section' : 'Generate this section' }}</Button></EmptyState>

              <template v-if="hasContent(current) && ws.id.value">
                <KnowledgeCheck :key="`kc-${current}`" :session-id="ws.id.value" :section-key="current" />
                <NotesBox :key="`n-${current}`" :section-key="current" :saved="st.note" @save="ws.setState(current, { note: $event })" />
              </template>
              <div class="flex items-center justify-between border-t pt-3">
                <Button variant="outline" :disabled="!prev" @click="prev && (current = prev.key)"><ArrowLeft /> {{ prev ? prev.title : 'Previous' }}</Button>
                <Button variant="outline" :disabled="!nextSec" @click="nextSec && (current = nextSec.key)">{{ nextSec ? nextSec.title : 'Next' }} <ArrowRight /></Button></div>
            </template>
          </Card>
          <p class="text-xs text-muted-foreground">AI-generated material can contain errors — verify important facts against official documentation. Alt+←/→ moves between sections.</p>
        </div>
        <SectionRail :rows="ws.rows.value" :current="current" :states="states" :progress="ws.reading.value" :next="recommended" @select="current = $event" @tutor="askTutor" />
      </div>
    </template>
  </div>
</template>
