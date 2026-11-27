<script setup lang="ts">
import { computed } from 'vue'
import { BarChart3, CheckCircle2, CircleDashed, Layers, Timer, Trophy } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Tooltip from '@/components/ui/Tooltip.vue'
import type { CareerMetrics } from '@/types/api'

const props = withDefaults(defineProps<{ metrics?: CareerMetrics; title?: string; loading?: boolean; career?: boolean }>(), { career: true })
const pct = (v: number) => `${Math.round(v * 100)}%`
const r = 38, C = 2 * Math.PI * r
const ready = computed(() => props.metrics?.readiness ?? 0)
const tiles = computed(() => {
  const m = props.metrics; if (!m) return []
  return [
    { key: 'coverage', icon: Layers, label: 'Skill coverage', value: `${m.skills_covered} / ${m.skills_total}`, sub: `${pct(m.coverage)} assessed`, tip: 'Share of this career’s skills with any validated evidence. Breadth only: it says nothing about how well you know them.' },
    { key: 'mastery', icon: BarChart3, label: 'Avg. mastery', value: pct(m.mastery), sub: 'across all skills', tip: 'Mean per-skill mastery = attainment × evidence reliability. Reading lessons does not raise it.' },
    { key: 'mastered', icon: Trophy, label: 'Mastered', value: String(m.mastered), sub: m.needs_revision ? `${m.needs_revision} need revision` : 'validated', tip: 'Skills at or above the required level, backed by at least one graded assessment.' },
    { key: 'learning', icon: Timer, label: 'In progress', value: String(m.in_progress), sub: 'learning', tip: 'Skills with evidence or study progress that have not reached the required level yet.' },
    { key: 'remaining', icon: CircleDashed, label: 'Remaining', value: String(m.remaining), sub: 'to master', tip: 'Skills not yet mastered, including ones that need revision.' },
  ]
})
</script>
<template>
  <Card class="p-4 sm:p-5" aria-label="Career overview">
    <div class="flex flex-col gap-5 lg:flex-row lg:items-center">
      <div class="flex items-center gap-4 lg:pr-6 lg:border-r">
        <Skeleton v-if="loading && !metrics" class="size-24 rounded-full" />
        <div v-else class="relative size-24 shrink-0" role="img" :aria-label="career ? `Career readiness ${pct(ready)}` : 'Career readiness: choose a career'">
          <svg viewBox="0 0 100 100" class="size-full -rotate-90">
            <circle cx="50" cy="50" :r="r" fill="none" stroke="var(--muted)" stroke-width="10" />
            <circle v-if="career" cx="50" cy="50" :r="r" fill="none" stroke="var(--primary)" stroke-width="10" stroke-linecap="round" :stroke-dasharray="C" :stroke-dashoffset="C * (1 - ready)" class="transition-[stroke-dashoffset] duration-500" />
          </svg>
          <div class="absolute inset-0 grid place-items-center text-xl font-semibold tabular-nums">{{ career ? pct(ready) : '—' }}</div>
        </div>
        <div>
          <Tooltip text="Importance-weighted validated mastery of the skills this career requires. Not a hiring probability."><h2 class="cursor-help text-sm font-semibold">Career readiness</h2></Tooltip>
          <p class="mt-0.5 text-xs text-muted-foreground">{{ title ?? '—' }}</p>
          <p v-if="metrics && career" class="mt-1 text-xs text-muted-foreground">Evidence confidence {{ pct(metrics.readiness_confidence) }}</p>
          <p v-else-if="metrics" class="mt-1 max-w-48 text-xs text-muted-foreground">Choose a career above to see your readiness.</p>
        </div>
      </div>
      <ul class="grid flex-1 grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 xl:grid-cols-5" aria-label="Career metrics">
        <template v-if="loading && !metrics"><li v-for="i in 5" :key="i"><Skeleton class="h-12" /></li></template>
        <li v-for="t in tiles" v-else :key="t.key"><Tooltip :text="t.tip">
          <div class="flex items-start gap-2.5" :data-metric="t.key">
            <span class="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-accent text-primary"><component :is="t.icon" class="size-4" aria-hidden="true" /></span>
            <div class="min-w-0"><p class="text-lg font-semibold leading-tight tabular-nums">{{ t.value }}</p><p class="text-xs text-muted-foreground">{{ t.label }}</p><p class="text-[11px] text-muted-foreground">{{ t.sub }}</p></div>
          </div>
        </Tooltip></li>
      </ul>
    </div>
  </Card>
</template>
