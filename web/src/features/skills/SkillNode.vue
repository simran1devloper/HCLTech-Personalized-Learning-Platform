<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import type { GraphNode } from '@/types/api'
import type { NodeRole } from './graph'
import { STATUS, UNMET_ICON, categoryColor, categoryLabel } from './status'

export interface SkillNodeData { skill: GraphNode; role: NodeRole; unmet: boolean }
const props = defineProps<{ data: SkillNodeData }>()
const emit = defineEmits<{ select: [id: string] }>()
const s = computed(() => props.data.skill)
const meta = computed(() => STATUS[s.value.status])
const pct = computed(() => Math.round(s.value.mastery * 100))
const missing = computed(() => (s.value.status === 'mastered' ? 0 : s.value.missing_prerequisites.length))
const label = computed(() => `${s.value.name}, ${categoryLabel(s.value.category)}, ${meta.value.label}${s.value.evidence_count ? `, mastery ${pct.value}%` : ''}${missing.value ? `, ${missing.value} prerequisite${missing.value > 1 ? 's' : ''} missing` : ''}`)
</script>
<template>
  <div :data-skill="s.skill_id" :data-status="s.status" :data-role="data.role" role="button" tabindex="0" :aria-label="label" :aria-pressed="data.role === 'selected'"
    :class="['w-[208px] cursor-pointer rounded-lg border px-3 py-2 text-left shadow-sm outline-none transition-[opacity,box-shadow] focus-visible:ring-2 focus-visible:ring-ring', meta.node,
      data.role === 'selected' && 'ring-2 ring-ring shadow-md', data.role === 'dimmed' && 'opacity-30', data.unmet && 'border-dashed !border-warning ring-2 ring-warning/40']"
    @keydown.enter.prevent="emit('select', s.skill_id)" @keydown.space.prevent="emit('select', s.skill_id)">
    <Handle type="target" :position="Position.Left" class="!size-1.5 !border-0 !bg-border" />
    <div class="flex items-center gap-1.5 text-[13px] font-semibold leading-tight">
      <component :is="meta.icon" :class="['size-4 shrink-0', meta.icontone]" aria-hidden="true" />
      <span class="truncate">{{ s.name }}</span>
      <component :is="UNMET_ICON" v-if="data.unmet" class="ml-auto size-3.5 shrink-0 text-warning" aria-hidden="true" />
    </div>
    <div class="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <span class="size-2 shrink-0 rounded-full" :style="{ background: categoryColor(s.category) }" aria-hidden="true" />
      <span class="truncate">{{ categoryLabel(s.category) }}</span>
      <span class="ml-auto shrink-0 tabular-nums">{{ meta.label }}</span>
    </div>
    <div class="mt-1.5 flex items-center gap-2" aria-hidden="true">
      <div class="h-1 flex-1 overflow-hidden rounded-full bg-black/10 dark:bg-white/15"><div class="h-full rounded-full bg-primary" :style="{ width: `${pct}%` }" /></div>
      <span class="w-7 text-right text-[10px] tabular-nums text-muted-foreground">{{ s.evidence_count ? `${pct}%` : '–' }}</span>
    </div>
    <div v-if="missing" class="mt-1 text-[10px] font-medium text-warning">{{ missing }} prerequisite{{ missing > 1 ? 's' : '' }} missing</div>
    <div v-else-if="!s.evidence_count && s.learning_progress" class="mt-1 text-[10px] text-muted-foreground">Studied {{ Math.round(s.learning_progress * 100) }}% · not assessed</div>
    <Handle type="source" :position="Position.Right" class="!size-1.5 !border-0 !bg-border" />
  </div>
</template>
