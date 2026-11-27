<script setup lang="ts">
import { computed } from 'vue'
import { Bot, CheckCircle2, Circle, ListTree, Loader2, Sparkles } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Progress from '@/components/ui/Progress.vue'
import type { TaskRow } from './monitor'
import type { SectionState } from '@/types/api'
import type { Progress as P } from './derive'

const props = defineProps<{ rows: TaskRow[]; current: string; states: Record<string, SectionState>; progress: P; next: { key: string; title: string } | null }>()
const emit = defineEmits<{ select: [key: string]; tutor: [] }>()
const currentTitle = computed(() => props.rows.find((r) => r.key === props.current)?.title ?? '')
</script>
<template>
  <aside class="space-y-3" aria-label="Study sidebar">
    <Card class="p-4"><h2 class="mb-2 flex items-center gap-2 text-sm font-semibold"><ListTree class="size-4 text-primary" aria-hidden="true" />Table of Contents</h2>
      <ul class="space-y-0.5 text-sm"><li v-for="r in rows" :key="r.key"><button type="button" :aria-current="current === r.key ? 'true' : undefined" :class="['flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left', current === r.key ? 'bg-accent font-medium' : 'hover:bg-accent/60', r.status === 'not_generated' && 'text-muted-foreground']" @click="emit('select', r.key)">
        <Loader2 v-if="r.status === 'running'" class="size-3.5 shrink-0 animate-spin" aria-hidden="true" /><CheckCircle2 v-else-if="states[r.key]?.understood" class="size-3.5 shrink-0 text-success" aria-hidden="true" /><Circle v-else class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span class="truncate">{{ r.title }}</span></button></li></ul></Card>
    <Card class="p-4"><div class="flex items-center justify-between text-sm"><h2 class="font-semibold">Your Progress</h2><span class="font-medium tabular-nums">{{ progress.readingPct }}%</span></div>
      <Progress :value="progress.opened" :max="Math.max(1, progress.total)" label="Sections read" class="mt-2 [&>div]:bg-success" />
      <p class="mt-1.5 text-xs text-muted-foreground" data-test="read-count">{{ progress.opened }} of {{ progress.total }} sections read · {{ progress.understood }} understood</p></Card>
    <Card v-if="next" class="p-4"><h2 class="mb-2 text-sm font-semibold">Recommended Next</h2>
      <button type="button" class="flex w-full items-center gap-2 rounded-md border p-2 text-left text-sm hover:bg-accent" @click="emit('select', next.key)"><Sparkles class="size-4 shrink-0 text-primary" aria-hidden="true" /><span class="min-w-0"><span class="block truncate font-medium">{{ next.title }}</span><span class="text-xs text-muted-foreground">Continue where you left off</span></span></button></Card>
    <Card class="space-y-2 bg-gradient-to-br from-accent/60 to-card p-4"><h2 class="flex items-center gap-2 text-sm font-semibold"><Bot class="size-4 text-primary" aria-hidden="true" />Ask AI Tutor</h2>
      <p class="text-xs text-muted-foreground">Get help with “{{ currentTitle }}”. The tutor opens with your question ready; nothing is sent until you press Send.</p>
      <Button size="sm" class="w-full" @click="emit('tutor')">Chat with AI Tutor</Button></Card>
  </aside>
</template>
