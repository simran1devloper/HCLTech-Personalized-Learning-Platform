<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { ArrowRight, CheckCircle2, Wrench } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import { tutorApi } from '@/services/api'
import { pct } from '@/utils/format'
import type { TutorTool } from '@/types/api'

/** One LearnAI action: a proposal awaiting confirmation, or the structured result of a module (real stored data). */
const props = defineProps<{ tool: TutorTool }>()
const qc = useQueryClient()
const override = ref<TutorTool | null>(null); const busy = ref(false)
const t = computed(() => override.value ?? props.tool)
const card = computed(() => (t.value.status === 'succeeded' ? t.value.result : null))
const data = computed<Record<string, any>>(() => card.value?.data ?? {})
const PROPOSAL: Record<string, (a: Record<string, any>) => string> = {
  generate_roadmap: (a) => `Create a roadmap (${a.hours_per_week} h/week)`,
  create_quiz: (a) => `Create a ${a.n_questions}-question quiz`,
}
const proposal = computed(() => PROPOSAL[t.value.tool]?.(t.value.args) ?? 'Run this action')

async function run(kind: 'confirm' | 'dismiss') {
  busy.value = true
  try {
    override.value = await tutorApi[kind](t.value.execution_id)
    if (kind === 'confirm' && override.value.status === 'succeeded') await Promise.all(['roadmaps', 'assessments', 'analytics'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
    await qc.invalidateQueries({ queryKey: ['tutor', 'conversation'] })
  } catch (e) { toast.error((e as Error).message) } finally { busy.value = false }
}
</script>

<template>
  <div class="surface-inset p-3 text-sm" data-testid="tool-card">
    <template v-if="t.status === 'pending' || (t.status === 'failed' && PROPOSAL[t.tool])">
      <p class="flex items-center gap-2 font-medium"><Wrench class="size-4 text-primary" aria-hidden="true" />{{ proposal }}</p>
      <p v-if="t.status === 'failed'" role="status" class="mt-1 text-destructive">{{ t.result?.message ?? 'The action failed.' }}</p>
      <div class="mt-2 flex gap-2"><Button size="sm" :loading="busy" @click="run('confirm')">{{ t.status === 'failed' ? 'Try again' : 'Confirm' }}</Button>
        <Button size="sm" variant="ghost" :disabled="busy" @click="run('dismiss')">Dismiss</Button></div>
    </template>
    <p v-else-if="t.status === 'dismissed'" class="text-muted-foreground">Action dismissed.</p>
    <p v-else-if="t.status === 'running'" role="status" class="text-muted-foreground">Running…</p>
    <p v-else-if="t.status === 'failed'" role="status" class="text-destructive">{{ t.result?.message ?? 'The action failed.' }}</p>
    <template v-else-if="card">
      <p class="flex items-center gap-2 font-medium"><CheckCircle2 class="size-4 text-success" aria-hidden="true" />{{ card.title }}</p>
      <p v-if="card.empty" class="mt-1 text-muted-foreground">{{ card.empty }}</p>
      <template v-else-if="card.kind === 'skill_gaps'">
        <div class="mt-2 overflow-x-auto"><table class="w-full text-left text-xs">
          <thead><tr class="text-muted-foreground"><th class="py-1 pr-3 font-medium">Skill</th><th class="pr-3 font-medium">Current</th><th class="pr-3 font-medium">Target</th><th class="font-medium">Evidence</th></tr></thead>
          <tbody><tr v-for="g in data.items" :key="g.name" class="border-t"><td class="py-1 pr-3">{{ g.name }} <Badge v-if="g.blocked" tone="warning">prerequisites first</Badge></td><td class="pr-3">{{ g.current == null ? '—' : g.current.toFixed(1) }}</td><td class="pr-3">{{ g.target.toFixed(1) }}</td><td>{{ g.confidence ? pct(g.confidence) + ' confidence' : 'not assessed' }}</td></tr></tbody></table></div>
        <p v-if="data.recommended_next" class="mt-2">Recommended next: <strong>{{ data.recommended_next }}</strong></p>
      </template>
      <template v-else-if="card.kind === 'assessment'">
        <p class="mt-1">Score <strong>{{ data.score_pct }}%</strong> · {{ data.correct_count }} of {{ data.total_questions }} correct<template v-if="data.level"> · {{ data.level }}</template></p>
        <ul v-if="data.concepts?.length" class="mt-2 flex flex-wrap gap-1.5"><li v-for="c in data.concepts" :key="c.concept"><Badge :tone="c.gap ? 'danger' : 'neutral'">{{ c.concept }} {{ pct(c.accuracy) }}</Badge></li></ul>
      </template>
      <template v-else-if="card.kind === 'roadmap'">
        <p class="mt-1 text-muted-foreground">{{ data.done }} of {{ data.total }} milestones done · about {{ data.estimated_weeks }} weeks at {{ data.hours_per_week }} h/week</p>
        <ol class="mt-1 list-decimal space-y-0.5 pl-5"><li v-for="(m, i) in data.milestones" :key="i">{{ m.skill }} → {{ m.target }}/5 <span class="text-xs text-muted-foreground">({{ m.hours }} h, {{ String(m.status).replace(/_/g, ' ') }})</span></li></ol>
      </template>
      <p v-else-if="card.kind === 'quiz'" class="mt-1 text-muted-foreground">{{ data.generating ? `${data.total_questions} of ${data.requested} questions ready; the rest are being generated.` : `${data.total_questions} questions.` }}</p>
      <ul v-else-if="card.kind === 'history'" class="mt-1 space-y-0.5"><li v-for="h in data.items" :key="h.link"><RouterLink :to="h.link" class="text-primary underline-offset-2 hover:underline">{{ h.title }}</RouterLink> <span class="text-xs text-muted-foreground">{{ h.completed }}/{{ h.sections }} sections</span></li></ul>
      <p v-else-if="card.kind === 'progress'" class="mt-1">{{ data.assessments_completed }} assessments · {{ data.skills_assessed }} skills assessed · {{ data.consistency?.current_streak_days }}-day streak<template v-if="data.roadmap_completion?.total"> · roadmap {{ data.roadmap_completion.done }}/{{ data.roadmap_completion.total }}</template></p>
      <RouterLink v-if="card.link" :to="card.link" class="mt-2 inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">{{ card.link_label }}<ArrowRight class="size-3.5" aria-hidden="true" /></RouterLink>
    </template>
  </div>
</template>
