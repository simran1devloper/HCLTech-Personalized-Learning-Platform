<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, CheckCircle2, RotateCcw, Sparkles, XCircle } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import EChart from '@/features/charts/EChart.vue'
import CodeView from '@/features/content/CodeView.vue'
import { LEVEL_LABEL, LEVEL_TONE, conceptAccuracyOption, describeConcepts, formatDuration } from './resultCharts'
import type { AssessmentResults } from '@/types/api'
import { pct } from '@/utils/format'

const props = defineProps<{ result: AssessmentResults; planLoading?: boolean; planError?: string | null; retaking?: boolean }>()
const emit = defineEmits<{ plan: []; retake: []; openPlan: [] }>()
const gaps = computed(() => props.result.skill_gaps)
const concepts = computed(() => props.result.concepts ?? [])
const hasGaps = computed(() => !!gaps.value && (gaps.value.recommended_learning_order.length > 0 || (gaps.value.unknown_concepts.length > 0 && (gaps.value.overall_score ?? 0) < 70)))
const lang = computed(() => (['Python', 'Data Structures and Algorithms'].includes(props.result.skill ?? '') ? 'python' : props.result.skill === 'SQL' || props.result.skill === 'PostgreSQL' ? 'sql' : 'text'))
const chosen = (r: { selected: unknown }, i: number) => (Array.isArray(r.selected) ? (r.selected as number[]).includes(i) : r.selected === i)
</script>

<template>
  <div class="space-y-4">
    <Card class="p-5" role="status">
      <p class="text-xs text-muted-foreground">Score (computed by the backend)</p>
      <div class="flex flex-wrap items-end gap-3"><p class="text-3xl font-semibold tabular-nums">{{ pct(result.score) }}</p>
        <Badge v-if="result.proficiency_level" :tone="LEVEL_TONE[result.proficiency_level]">{{ LEVEL_LABEL[result.proficiency_level] }}</Badge></div>
      <dl class="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div><dt class="text-xs text-muted-foreground">Correct</dt><dd class="font-medium tabular-nums">{{ result.correct_count ?? '—' }}</dd></div>
        <div><dt class="text-xs text-muted-foreground">Incorrect</dt><dd class="font-medium tabular-nums">{{ result.incorrect_count ?? '—' }}</dd></div>
        <div><dt class="text-xs text-muted-foreground">Time taken</dt><dd class="font-medium">{{ formatDuration(result.time_taken_seconds) }}</dd></div>
        <div><dt class="text-xs text-muted-foreground">Question mix</dt><dd class="font-medium capitalize">{{ result.difficulty_level ?? '—' }}</dd></div>
      </dl>
      <p v-if="result.proficiency" class="mt-3 text-sm">Current proficiency: <strong class="tabular-nums">{{ result.proficiency.proficiency_score.toFixed(1) }} / 5</strong> · confidence {{ pct(result.proficiency.confidence_score) }} · {{ result.proficiency.evidence_count }} evidence item(s)
        <span class="text-xs text-muted-foreground">({{ result.proficiency.proficiency_version }})</span></p>
    </Card>

    <Card v-if="concepts.length" class="space-y-3 p-5">
      <h2 class="text-base font-semibold">Concept performance</h2>
      <EChart :option="conceptAccuracyOption(concepts)" label="Accuracy by concept" :summary="describeConcepts(concepts)" :height="Math.max(160, concepts.length * 44 + 24)" />
      <table class="w-full text-sm"><caption class="sr-only">Accuracy by concept</caption>
        <thead><tr class="border-b text-left text-xs text-muted-foreground"><th scope="col" class="py-1.5 font-medium">Concept</th><th scope="col" class="font-medium">Accuracy</th><th scope="col" class="font-medium">Questions</th><th scope="col" class="font-medium">Result</th></tr></thead>
        <tbody><tr v-for="c in [...concepts].sort((a, b) => a.accuracy - b.accuracy)" :key="c.concept" class="border-b last:border-0"><th scope="row" class="py-1.5 text-left font-medium">{{ c.concept }}</th>
          <td class="tabular-nums">{{ Math.round(c.accuracy * 100) }}%</td><td class="tabular-nums">{{ c.questions_attempted }}</td><td><Badge :tone="LEVEL_TONE[c.proficiency_level]">{{ LEVEL_LABEL[c.proficiency_level] }}</Badge></td></tr></tbody></table>
    </Card>

    <Card v-if="gaps" class="space-y-4 p-5">
      <h2 class="text-base font-semibold">Skill-gap analysis</h2>
      <div class="grid gap-4 sm:grid-cols-2">
        <div v-if="gaps.critical_gaps.length"><h3 class="text-sm font-medium">Critical gaps</h3><ul class="mt-1 flex flex-wrap gap-1.5"><li v-for="c in gaps.critical_gaps" :key="c"><Badge tone="danger">{{ c }}</Badge></li></ul></div>
        <div v-if="gaps.weak_concepts.length"><h3 class="text-sm font-medium">Weak concepts</h3><ul class="mt-1 flex flex-wrap gap-1.5"><li v-for="c in gaps.weak_concepts" :key="c"><Badge tone="warning">{{ c }}</Badge></li></ul></div>
        <div v-if="gaps.strong_concepts.length"><h3 class="text-sm font-medium">Strong concepts</h3><ul class="mt-1 flex flex-wrap gap-1.5"><li v-for="c in gaps.strong_concepts" :key="c"><Badge tone="success">{{ c }}</Badge></li></ul></div>
        <div v-if="gaps.unknown_concepts.length"><h3 class="text-sm font-medium">Not enough data</h3><ul class="mt-1 flex flex-wrap gap-1.5"><li v-for="c in gaps.unknown_concepts" :key="c"><Badge>{{ c }}</Badge></li></ul></div>
      </div>
      <p v-if="!gaps.critical_gaps.length && !gaps.weak_concepts.length" class="text-sm text-muted-foreground">No concept fell below 50% with enough questions to be sure.</p>
      <div v-if="gaps.recommended_learning_order.length"><h3 class="text-sm font-medium">Recommended learning order</h3><ol class="mt-1 list-decimal space-y-0.5 pl-5 text-sm"><li v-for="c in gaps.recommended_learning_order" :key="c">{{ c }}</li></ol></div>
      <div v-if="gaps.prerequisite_gaps.length"><h3 class="text-sm font-medium">Prerequisites to refresh</h3><ul class="mt-1 list-disc pl-5 text-sm"><li v-for="p in gaps.prerequisite_gaps" :key="p.skill_id">{{ p.name }} <span class="text-xs text-muted-foreground">({{ p.current == null ? 'not assessed' : `level ${p.current.toFixed(1)}` }}, needs level {{ p.required }})</span></li></ul></div>
      <p class="text-xs text-muted-foreground">{{ gaps.caveat }}</p>
    </Card>

    <Card class="flex flex-wrap items-center justify-between gap-3 p-5">
      <div class="min-w-0"><h2 class="text-base font-semibold">Your next step</h2>
        <p class="text-sm text-muted-foreground">{{ hasGaps ? 'A short plan covering only your gaps, with prerequisites, diagrams, code and practice.' : 'No gaps were found in this attempt. Retake with more questions to look deeper.' }}</p>
        <p v-if="planError" role="alert" class="mt-1 text-sm text-destructive">{{ planError }}</p></div>
      <div class="flex flex-wrap gap-2">
        <Button v-if="result.learning_session_id" @click="emit('openPlan')"><ArrowRight /> Open my learning plan</Button>
        <Button v-else :disabled="!hasGaps" :loading="planLoading" @click="emit('plan')"><Sparkles /> Generate My Personalized Learning Plan</Button>
        <Button variant="outline" :loading="retaking" @click="emit('retake')"><RotateCcw /> Retake assessment</Button>
      </div>
    </Card>

    <section aria-labelledby="review-h" class="space-y-3"><h2 id="review-h" class="text-base font-semibold">Review answers</h2>
      <ol class="space-y-3">
        <li v-for="(r, i) in result.review ?? []" :key="r.question_id"><Card class="p-4">
          <p class="flex items-start gap-2 text-sm font-medium"><component :is="r.is_correct ? CheckCircle2 : XCircle" :class="['mt-0.5 size-4 shrink-0', r.is_correct ? 'text-success' : 'text-destructive']" aria-hidden="true" /><span>{{ i + 1 }}. {{ r.prompt }}</span></p>
          <CodeView v-if="r.code_snippet" :code="r.code_snippet" :lang="lang" />
          <ul class="mt-2 space-y-1 text-sm"><li v-for="(o, oi) in r.options" :key="oi" :class="['rounded-md border px-2.5 py-1.5', r.correct_options.includes(oi) ? 'border-success bg-success/10' : chosen(r, oi) ? 'border-destructive bg-destructive/10' : '']">
            {{ o }}<span v-if="r.correct_options.includes(oi)" class="ml-2 text-xs font-medium text-success">Correct answer</span><span v-if="chosen(r, oi) && !r.correct_options.includes(oi)" class="ml-2 text-xs font-medium text-destructive">Your answer</span><span v-else-if="chosen(r, oi)" class="ml-1 text-xs text-muted-foreground">(your answer)</span></li></ul>
          <p v-if="r.selected == null" class="mt-1 text-xs text-muted-foreground">Not answered.</p>
          <p class="mt-2 text-sm text-muted-foreground">{{ r.explanation }}</p>
          <p class="mt-1 text-xs text-muted-foreground">{{ r.concept }} · difficulty {{ r.difficulty }}/5</p></Card></li>
      </ol></section>
  </div>
</template>
