<script setup lang="ts">
import { computed, ref } from 'vue'
import { useVirtualList } from '@vueuse/core'
import { AlertTriangle } from 'lucide-vue-next'
import type { GraphNode } from '@/types/api'
import { STATUS, categoryColor, categoryLabel, hoursLabel } from './status'

const props = defineProps<{ nodes: GraphNode[]; selectedId: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const sort = ref<'order' | 'mastery' | 'name'>('order')
const rows = computed(() => [...props.nodes].sort(sort.value === 'name' ? (a, b) => a.name.localeCompare(b.name)
  : sort.value === 'mastery' ? (a, b) => b.mastery - a.mastery || a.name.localeCompare(b.name) : (a, b) => a.depth - b.depth || a.name.localeCompare(b.name)))
const ROW = 56
// Only the rows in view are in the DOM, so a list of thousands of skills stays responsive.
const { list, containerProps, wrapperProps } = useVirtualList(rows, { itemHeight: ROW, overscan: 8 })
</script>
<template>
  <div class="flex h-full flex-col">
    <div class="flex items-center justify-between gap-2 border-b px-4 py-2 text-xs text-muted-foreground">
      <span aria-live="polite">{{ nodes.length }} skill{{ nodes.length === 1 ? '' : 's' }}</span>
      <label class="flex items-center gap-1.5">Sort
        <select v-model="sort" aria-label="Sort skills" class="h-7 field px-1.5 text-xs text-foreground">
          <option value="order">Learning order</option><option value="mastery">Mastery</option><option value="name">Name</option>
        </select></label>
    </div>
    <div v-if="!nodes.length" class="grid flex-1 place-items-center p-8 text-sm text-muted-foreground">No skills match these filters.</div>
    <div v-else v-bind="containerProps" class="flex-1 overflow-y-auto" role="list" aria-label="Skills">
      <div v-bind="wrapperProps">
        <div v-for="{ data: n } in list" :key="n.skill_id" role="listitem" :style="{ height: `${ROW}px` }">
          <button type="button" :data-skill="n.skill_id" :aria-pressed="n.skill_id === selectedId"
            :class="['flex size-full items-center gap-3 border-b px-4 text-left text-sm hover:bg-accent focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring', n.skill_id === selectedId && 'bg-accent']" @click="emit('select', n.skill_id)">
            <component :is="STATUS[n.status].icon" :class="['size-4 shrink-0', STATUS[n.status].icontone]" aria-hidden="true" />
            <span class="min-w-0 flex-1">
              <span class="block truncate font-medium">{{ n.name }}<span v-if="n.critical" class="ml-1.5 rounded bg-destructive/10 px-1 text-[10px] font-medium text-destructive">critical</span></span>
              <span class="flex items-center gap-1.5 text-xs text-muted-foreground"><span class="size-2 rounded-full" :style="{ background: categoryColor(n.category) }" aria-hidden="true" />{{ categoryLabel(n.category) }} · {{ STATUS[n.status].label }}
                <span v-if="n.missing_prerequisites.length" class="inline-flex items-center gap-0.5 text-warning"><AlertTriangle class="size-3" aria-hidden="true" />{{ n.missing_prerequisites.length }} prerequisite{{ n.missing_prerequisites.length > 1 ? 's' : '' }} missing</span></span>
            </span>
            <span class="hidden w-28 shrink-0 sm:block" :aria-label="`Mastery ${Math.round(n.mastery * 100)}%`">
              <span class="block h-1.5 overflow-hidden rounded-full bg-muted"><span class="block h-full rounded-full bg-primary" :style="{ width: `${n.mastery * 100}%` }" /></span>
              <span class="mt-0.5 block text-right text-[11px] tabular-nums text-muted-foreground">{{ n.proficiency.toFixed(1) }} / {{ n.target }}</span>
            </span>
            <span class="hidden w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground md:block">{{ hoursLabel(n.hours_remaining) }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
