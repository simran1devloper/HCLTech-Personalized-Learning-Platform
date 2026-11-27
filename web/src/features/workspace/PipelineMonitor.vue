<script setup lang="ts">
import { computed, ref } from 'vue'
import { Archive, CheckCircle2, ChevronDown, Circle, Clock, Loader2, RotateCcw, Sparkles, Square, XCircle } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Progress from '@/components/ui/Progress.vue'
import { etaSeconds, summarizeRows, type TaskRow, type TaskStatus } from './monitor'

/** Compact generation monitor. Everything shown is real: statuses come from the live SSE events (or the persisted server report after a refresh),
 *  durations and tokens are measured, the time estimate is derived from this run's finished sections. */
const props = defineProps<{ rows: TaskRow[]; running: boolean; topic?: string; tokens?: { input: number; output: number }; error?: string | null }>()
const emit = defineEmits<{ cancel: []; retry: [key: string]; continue: [] }>()
const open = ref(false)
const sum = computed(() => summarizeRows(props.rows))
const eta = computed(() => (props.running ? etaSeconds(props.rows) : null))
const state = computed(() => (props.running ? 'running' : sum.value.failed ? 'attention' : sum.value.finished === sum.value.total ? 'done' : 'paused'))
const LABEL: Record<TaskStatus, string> = { queued: 'Queued', running: 'Running', completed: 'Completed', reused: 'Reused', failed: 'Failed', not_generated: 'Not generated' }
const secs = (ms?: number) => (ms ? (ms >= 10_000 ? `${Math.round(ms / 1000)}s` : `${(ms / 1000).toFixed(1)}s`) : '')
const clock = (s: number) => (s >= 90 ? `~${Math.round(s / 60)} min` : `~${s}s`)
</script>

<template>
  <Card class="space-y-3 p-4" aria-label="Study material pipeline" role="region">
    <div class="flex flex-wrap items-center gap-3">
      <span class="grid size-8 shrink-0 place-items-center rounded-md bg-accent text-primary"><Sparkles class="size-4" aria-hidden="true" /></span>
      <div class="min-w-[12rem] flex-1">
        <div class="flex flex-wrap items-center gap-2"><h2 class="text-sm font-semibold">Study Material Generation</h2>
          <Badge :tone="state === 'running' ? 'warning' : state === 'done' ? 'success' : state === 'attention' ? 'danger' : 'neutral'">
            {{ state === 'running' ? 'Running in background' : state === 'done' ? 'Complete' : state === 'attention' ? 'Needs attention' : 'Paused' }}</Badge></div>
        <p class="truncate text-xs text-muted-foreground">{{ running ? `Generating personalized study material${topic ? ' for ' + topic : ''}…` : `${sum.finished} of ${sum.total} sections ready${sum.reused ? ` · ${sum.reused} reused, no model call` : ''}` }}</p>
      </div>
      <div class="order-last flex w-full items-center gap-2 sm:order-none sm:w-auto"><Progress :value="sum.finished" :max="Math.max(1, sum.total)" label="Generation progress" class="flex-1 sm:w-40 sm:flex-none" /><span class="text-sm font-medium tabular-nums">{{ sum.pct }}%</span></div>
      <div class="flex items-center gap-2">
        <span v-if="eta" class="hidden items-center gap-1 text-xs text-muted-foreground sm:inline-flex"><Clock class="size-3.5" aria-hidden="true" />{{ clock(eta) }} left</span>
        <Button v-if="running" size="sm" variant="outline" @click="emit('cancel')"><Square /> Cancel</Button>
        <Button v-else-if="sum.missing.length" size="sm" @click="emit('continue')"><RotateCcw /> {{ sum.failed ? 'Retry failed' : 'Continue generating' }}</Button>
        <Button size="icon" variant="ghost" :aria-expanded="open" :aria-label="open ? 'Collapse details' : 'Expand details'" @click="open = !open"><ChevronDown :class="['transition-transform', open && 'rotate-180']" /></Button>
      </div>
    </div>
    <p v-if="error && !running" role="alert" class="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">{{ error }}</p>

    <div v-if="open" class="grid gap-x-8 gap-y-1.5 border-t pt-3 sm:grid-cols-2" data-test="pipeline-details">
      <div v-for="r in rows" :key="r.key" class="flex items-center gap-2 text-sm" :data-task="r.key" :data-status="r.status">
        <Loader2 v-if="r.status === 'running'" class="size-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
        <CheckCircle2 v-else-if="r.status === 'completed'" class="size-4 shrink-0 text-success" aria-hidden="true" />
        <Archive v-else-if="r.status === 'reused'" class="size-4 shrink-0 text-primary" aria-hidden="true" />
        <XCircle v-else-if="r.status === 'failed'" class="size-4 shrink-0 text-destructive" aria-hidden="true" />
        <Circle v-else class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span class="min-w-0 flex-1 truncate">{{ r.title }}</span>
        <span class="shrink-0 text-xs text-muted-foreground"><span class="sr-only">Status: </span>{{ LABEL[r.status] }}<template v-if="secs(r.durationMs)"> · {{ secs(r.durationMs) }}</template><template v-if="r.tokens"> · {{ r.tokens }} tok</template><template v-if="(r.attempts ?? 0) > 1"> · attempt {{ r.attempts }}</template></span>
        <Button v-if="r.status === 'failed' && !running" size="sm" variant="outline" class="h-6 px-2" :aria-label="`Retry ${r.title}`" @click="emit('retry', r.key)"><RotateCcw class="size-3" /></Button>
        <p v-if="r.status === 'failed' && r.error" class="sr-only">{{ r.error }}</p>
      </div>
      <p v-if="rows.some((r) => r.status === 'failed' && r.error)" class="col-span-full text-xs text-destructive">{{ rows.filter((r) => r.status === 'failed').map((r) => `${r.title}: ${r.error}`).join(' · ') }}</p>
      <p v-if="tokens && (tokens.input || tokens.output)" class="col-span-full text-xs text-muted-foreground">Tokens used so far: {{ tokens.input }} in / {{ tokens.output }} out. Reused sections cost none.</p>
    </div>
  </Card>
</template>
