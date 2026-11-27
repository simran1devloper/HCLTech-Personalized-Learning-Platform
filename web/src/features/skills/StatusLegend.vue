<script setup lang="ts">
import { STATUS, STATUS_ORDER } from './status'
import type { SkillStatus } from '@/types/api'
defineProps<{ active: SkillStatus[]; counts: Partial<Record<SkillStatus, number>> }>()
defineEmits<{ toggle: [s: SkillStatus] }>()
</script>
<template>
  <div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by status">
    <button v-for="s in STATUS_ORDER" :key="s" type="button" :aria-pressed="active.includes(s)" :title="STATUS[s].hint"
      :class="['inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors', active.includes(s) ? 'border-primary bg-accent font-medium text-accent-foreground' : 'bg-card text-muted-foreground hover:bg-accent']"
      @click="$emit('toggle', s)">
      <span :class="['size-2 rounded-full', STATUS[s].dot]" aria-hidden="true" />{{ STATUS[s].label }}<span class="tabular-nums">{{ counts[s] ?? 0 }}</span>
    </button>
  </div>
</template>
