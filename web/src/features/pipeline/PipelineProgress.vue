<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertTriangle, Check, ChevronDown, CircleDashed, Loader2, MinusCircle, RotateCw, XCircle } from 'lucide-vue-next'
import Badge from '@/components/ui/Badge.vue'
import Progress from '@/components/ui/Progress.vue'
import { formatMs, summarize, type PipelineStage, type PipelineState } from './pipeline'
import { stageIcon } from './icons'

/** Live checklist of an agentic pipeline run. Every row is a real checkpoint reported by the backend
 *  (status, attempt number, duration, what it found); nothing here is animated on a timer. */
const props = defineProps<{ title: string; stages: PipelineStage[]; state: PipelineState; elapsedMs: number; description?: string }>()
const open = ref(props.state !== 'completed')   // a finished run is shown collapsed; a running one is expanded
watch(() => props.state, (s, was) => { if (was === 'running' && s === 'completed') open.value = false; if (s === 'running') open.value = true })

const sum = computed(() => summarize(props.stages))
const badge = computed(() => ({
  running: { tone: 'info', text: 'Running' }, completed: { tone: 'success', text: 'Completed' },
  attention: { tone: 'warning', text: 'Needs attention' }, cancelled: { tone: 'neutral', text: 'Stopped' }, idle: { tone: 'neutral', text: 'Idle' },
} as const)[props.state])
const meta: Record<string, { icon: any; text: string; cls: string }> = {
  pending: { icon: CircleDashed, text: 'Waiting', cls: 'text-muted-foreground' },
  running: { icon: Loader2, text: 'In progress', cls: 'text-primary' },
  retrying: { icon: RotateCw, text: 'Retrying', cls: 'text-warning' },
  done: { icon: Check, text: 'Done', cls: 'text-success' },
  failed: { icon: XCircle, text: 'Failed', cls: 'text-destructive' },
  skipped: { icon: MinusCircle, text: 'Skipped', cls: 'text-muted-foreground' },
}
const announce = computed(() => {
  const a = sum.value.active[0]
  return props.state === 'running' && a ? `${a.label}: ${meta[a.status].text}${a.attempt && a.attempt > 1 ? `, attempt ${a.attempt}` : ''}` : props.state === 'completed' ? 'All steps completed' : ''
})
</script>

<template>
  <section class="surface surface-raised" :aria-label="title" :aria-busy="state === 'running'">
    <header class="space-y-2 p-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2"><h2 class="text-sm font-semibold">{{ title }}</h2><Badge :tone="badge.tone">{{ badge.text }}</Badge></div>
        <div class="flex items-center gap-3 text-xs text-muted-foreground">
          <span class="tabular-nums" aria-label="Elapsed time">{{ formatMs(Math.floor(elapsedMs / 1000) * 1000) || '0 ms' }}</span>
          <span class="tabular-nums">{{ sum.done }} / {{ sum.total }} steps</span>
          <button type="button" class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent" :aria-expanded="open" @click="open = !open">
            {{ open ? 'Hide' : 'Show' }} steps <ChevronDown :class="['size-3.5 transition-transform', open && 'rotate-180']" aria-hidden="true" />
          </button>
        </div>
      </div>
      <Progress :value="sum.finished" :max="Math.max(1, sum.total)" label="Pipeline progress" />
      <p v-if="description" class="text-xs text-muted-foreground">{{ description }}</p>
    </header>
    <ol v-show="open" class="divide-y border-t">
      <li v-for="s in stages" :key="s.id" class="flex items-start gap-3 px-4 py-2.5" :data-status="s.status" :data-stage="s.id">
        <component :is="meta[s.status].icon" :class="['mt-0.5 size-4 shrink-0', meta[s.status].cls, (s.status === 'running') && 'animate-spin']" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p class="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span :class="['inline-flex items-center gap-1.5 font-medium', s.status === 'pending' && 'text-muted-foreground']">
              <component :is="stageIcon(s)" v-if="stageIcon(s)" class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" data-testid="stage-icon" />{{ s.label }}</span>
            <span :class="['text-xs', meta[s.status].cls]">{{ meta[s.status].text }}<template v-if="s.attempt && s.attempt > 1 || s.status === 'retrying'"> · attempt {{ s.attempt }}<template v-if="s.max_attempts"> of {{ s.max_attempts }}</template></template></span>
          </p>
          <p v-if="s.detail" class="mt-0.5 text-xs text-muted-foreground">{{ s.detail }}</p>
        </div>
        <span v-if="s.duration_ms != null" class="shrink-0 text-xs tabular-nums text-muted-foreground">{{ formatMs(s.duration_ms) }}</span>
      </li>
    </ol>
    <p v-if="state === 'attention'" class="flex items-center gap-1.5 border-t px-4 py-2 text-xs text-warning"><AlertTriangle class="size-3.5" aria-hidden="true" />Some steps did not finish. The steps above show where and why.</p>
    <p class="sr-only" role="status" aria-live="polite">{{ announce }}</p>
  </section>
</template>
