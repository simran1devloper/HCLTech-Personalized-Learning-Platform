<script setup lang="ts">
import { Bookmark, CheckCircle2, Loader2 } from 'lucide-vue-next'
import type { TaskRow } from './monitor'
import type { SectionState } from '@/types/api'

/** Sticky section navigation. Arrow keys move between tabs (roving tabindex); status dots show ready / understood / bookmarked / writing. */
defineProps<{ rows: TaskRow[]; current: string; states: Record<string, SectionState> }>()
const emit = defineEmits<{ select: [key: string] }>()
function onKey(e: KeyboardEvent, i: number, keys: string[]) {
  const n = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? keys.length - 1 : null
  if (n === null) return
  e.preventDefault(); const k = keys[(n + keys.length) % keys.length]; emit('select', k)
  ;(e.currentTarget as HTMLElement).parentElement?.querySelector<HTMLElement>(`[data-key="${k}"]`)?.focus()
}
</script>
<template>
  <nav aria-label="Study sections" class="sticky top-14 z-20 -mx-1 border-b bg-background/95 px-1 backdrop-blur">
    <div role="tablist" aria-label="Study sections" class="flex gap-1 overflow-x-auto py-1.5">
      <button v-for="(r, i) in rows" :key="r.key" type="button" role="tab" :id="`tab-${r.key}`" :data-key="r.key" :aria-selected="current === r.key" :aria-controls="`panel-${r.key}`" :tabindex="current === r.key ? 0 : -1"
        :class="['inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm', current === r.key ? 'bg-accent font-medium text-accent-foreground shadow-sm' : 'text-muted-foreground hover:bg-accent/60', r.status === 'not_generated' && 'opacity-70']"
        @click="emit('select', r.key)" @keydown="onKey($event, i, rows.map((x) => x.key))">
        {{ r.title }}
        <Loader2 v-if="r.status === 'running'" class="size-3.5 animate-spin" aria-label="writing" />
        <CheckCircle2 v-else-if="states[r.key]?.understood" class="size-3.5 text-success" aria-label="understood" />
        <Bookmark v-if="states[r.key]?.bookmarked" class="size-3.5 fill-current text-primary" aria-label="bookmarked" />
      </button>
    </div>
  </nav>
</template>
