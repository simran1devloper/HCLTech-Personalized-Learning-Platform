<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { AlertTriangle, BookOpen, CheckCircle2, ClipboardCheck, Clock, ExternalLink, GitBranch, Network, PlayCircle, RotateCcw, X, XCircle } from 'lucide-vue-next'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import CardContent from '@/components/ui/CardContent.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Unavailable from '@/components/ui/Unavailable.vue'
import { explorerApi } from '@/services/api'
import { dateShort } from '@/utils/format'
import type { GraphNode, SkillDetail } from '@/types/api'
import { STATUS, categoryColor, categoryLabel, hoursLabel, levelLabel } from './status'

const props = defineProps<{ node: GraphNode; detail?: SkillDetail; pending: boolean; error?: string; focused: boolean }>()
const emit = defineEmits<{ close: []; select: [id: string]; start: [id: string]; revise: [id: string]; assess: [id: string]; focus: [on: boolean]; retry: [] }>()
const meta = computed(() => STATUS[props.node.status])
const pct = computed(() => Math.round(props.node.mastery * 100))
const R = 30, C = 2 * Math.PI * R
const canRevise = computed(() => props.node.evidence_count > 0 || ['mastered', 'needs_revision', 'in_progress'].includes(props.node.status))
const missing = computed(() => props.detail?.missing_prerequisites ?? [])

const showResources = ref(false)
watch(() => props.node.skill_id, () => { showResources.value = false })
const resources = useQuery({ queryKey: computed(() => ['skill-resources', props.node.skill_id]), queryFn: () => explorerApi.resources(props.node.name, 5), enabled: showResources, staleTime: 10 * 60_000 })
const safeUrl = (u: unknown) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : undefined)
</script>
<template>
  <Card class="flex flex-col" data-test="skill-detail" :aria-label="`${node.name} details`">
    <div class="flex items-start justify-between gap-3 p-5 pb-2">
      <div class="min-w-0">
        <h2 class="truncate text-lg font-semibold leading-tight">{{ node.name }}</h2>
        <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span :class="['inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium', meta.chip]"><component :is="meta.icon" class="size-3.5" aria-hidden="true" />{{ meta.label }}</span>
          <span class="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><span class="size-2 rounded-full" :style="{ background: categoryColor(node.category) }" aria-hidden="true" />{{ categoryLabel(node.category) }}</span>
          <Badge v-if="node.critical" tone="danger">Critical</Badge>
        </div>
      </div>
      <Button variant="ghost" size="icon" aria-label="Clear selection" @click="emit('close')"><X /></Button>
    </div>
    <CardContent class="space-y-5 text-sm">
      <p class="text-xs text-muted-foreground">{{ meta.hint }}<template v-if="node.revision_reason"> {{ node.revision_reason === 'stale' ? 'Your latest evidence is more than 90 days old.' : 'Your latest result fell well below the level you reached before.' }}</template></p>
      <p v-if="node.description" class="text-muted-foreground">{{ node.description }}</p>

      <div class="flex items-center gap-4">
        <div class="relative size-[76px] shrink-0" role="img" :aria-label="`Mastery ${pct}%`">
          <svg viewBox="0 0 76 76" class="size-full -rotate-90"><circle cx="38" cy="38" :r="R" fill="none" stroke="var(--muted)" stroke-width="8" />
            <circle cx="38" cy="38" :r="R" fill="none" stroke="var(--primary)" stroke-width="8" stroke-linecap="round" :stroke-dasharray="C" :stroke-dashoffset="C * (1 - node.mastery)" /></svg>
          <span class="absolute inset-0 grid place-items-center text-base font-semibold tabular-nums">{{ pct }}%</span>
        </div>
        <dl class="grid flex-1 grid-cols-2 gap-x-3 gap-y-2 text-xs">
          <div><dt class="text-muted-foreground">Mastery level</dt><dd class="font-semibold">{{ levelLabel(node.proficiency) }}</dd></div>
          <div><dt class="text-muted-foreground">Proficiency</dt><dd class="font-semibold tabular-nums">{{ node.proficiency.toFixed(1) }} / {{ node.target }} <span class="font-normal text-muted-foreground">required</span></dd></div>
          <div><dt class="text-muted-foreground">Difficulty</dt><dd class="font-semibold tabular-nums">{{ node.difficulty }} / 5</dd></div>
          <div><dt class="flex items-center gap-1 text-muted-foreground"><Clock class="size-3" aria-hidden="true" />Est. learning time</dt><dd class="font-semibold">{{ hoursLabel(node.hours_remaining) }}</dd></div>
          <div v-if="node.evidence_count"><dt class="text-muted-foreground">Evidence confidence</dt><dd class="font-semibold tabular-nums">{{ Math.round(node.confidence * 100) }}%</dd></div>
          <div v-if="node.is_target"><dt class="text-muted-foreground">Career importance</dt><dd class="font-semibold tabular-nums">{{ Math.round(node.importance * 100) }}%</dd></div>
        </dl>
      </div>
      <div v-if="node.learning_progress > 0" class="text-xs">
        <div class="mb-1 flex justify-between text-muted-foreground"><span>Study progress</span><span class="tabular-nums">{{ Math.round(node.learning_progress * 100) }}%</span></div>
        <div class="h-1.5 overflow-hidden rounded-full bg-muted"><div class="h-full rounded-full bg-primary/60" :style="{ width: `${node.learning_progress * 100}%` }" /></div>
        <p class="mt-1 text-muted-foreground">Studying does not count as mastery until an assessment confirms it.</p>
      </div>

      <div v-if="missing.length" class="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs" role="alert">
        <p class="flex items-center gap-1.5 font-medium text-warning"><AlertTriangle class="size-3.5" aria-hidden="true" />Prerequisites missing</p>
        <p class="mt-1 text-muted-foreground">Start with <button type="button" class="font-medium text-foreground underline" @click="emit('select', missing[0].skill_id)">{{ missing[0].name }}</button><template v-if="missing.length > 1"> (then {{ missing.slice(1, 3).map((m) => m.name).join(', ') }})</template>.</p>
      </div>

      <div class="grid gap-2">
        <Button class="w-full" @click="emit('start', node.skill_id)"><PlayCircle />{{ node.evidence_count || node.learning_progress ? 'Continue learning' : 'Start learning' }}</Button>
        <div class="grid grid-cols-2 gap-2">
          <Button variant="outline" @click="emit('assess', node.skill_id)"><ClipboardCheck />Take assessment</Button>
          <Button variant="outline" :disabled="!canRevise" :title="canRevise ? undefined : 'Nothing to revise yet'" @click="emit('revise', node.skill_id)"><RotateCcw />Revise skill</Button>
          <Button variant="outline" :aria-pressed="showResources" @click="showResources = !showResources"><BookOpen />View resources</Button>
          <Button variant="outline" :aria-pressed="focused" @click="emit('focus', !focused)"><Network />{{ focused ? 'Show all skills' : 'Related skills' }}</Button>
        </div>
      </div>

      <section v-if="showResources" aria-label="Resources" data-test="resources">
        <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Resources</h3>
        <Skeleton v-if="resources.isPending.value" class="h-12" />
        <ErrorState v-else-if="resources.isError.value" :message="(resources.error.value as Error).message" @retry="resources.refetch()" />
        <Unavailable v-else-if="!resources.data.value?.items.length" :reason="resources.data.value?.sources_configured ? 'no resources matched this skill.' : 'no external resource source is configured on this server.'" />
        <ul v-else class="space-y-1"><li v-for="(r, i) in resources.data.value.items" :key="i" class="flex items-center justify-between gap-2 text-xs">
          <a v-if="safeUrl(r.resource.url)" :href="safeUrl(r.resource.url)" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">{{ r.resource.title ?? r.resource.url }}<ExternalLink class="size-3" aria-hidden="true" /></a>
          <span v-else>{{ r.resource.title ?? 'Resource' }}</span></li></ul>
      </section>

      <Skeleton v-if="pending && !detail" class="h-40" />
      <ErrorState v-else-if="error && !detail" :message="error" @retry="emit('retry')" />
      <template v-else-if="detail">
        <section aria-label="Prerequisites">
          <h3 class="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground"><GitBranch class="size-3.5" aria-hidden="true" />Prerequisites</h3>
          <p v-if="!detail.prerequisites.length" class="text-xs text-muted-foreground">None: this is a foundation skill.</p>
          <ul v-else class="space-y-1">
            <li v-for="p in detail.prerequisites" :key="p.skill_id" class="flex items-center justify-between gap-2">
              <button type="button" class="flex items-center gap-1.5 hover:underline" @click="emit('select', p.skill_id)">
                <CheckCircle2 v-if="p.met" class="size-4 text-success" aria-label="Met" /><XCircle v-else class="size-4 text-warning" aria-label="Not met" />{{ p.name }}</button>
              <span class="text-xs tabular-nums text-muted-foreground">{{ p.proficiency.toFixed(1) }} / {{ p.required_level }}</span></li>
          </ul>
        </section>
        <section aria-label="Skills unlocked">
          <h3 class="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Skills unlocked</h3>
          <p v-if="!detail.unlocks.length" class="text-xs text-muted-foreground">No other skill requires this one.</p>
          <ul v-else class="flex flex-wrap gap-1"><li v-for="u in detail.unlocks" :key="u.skill_id"><button type="button" class="rounded-md bg-muted px-2 py-0.5 text-xs hover:bg-accent" @click="emit('select', u.skill_id)">{{ u.name }}</button></li></ul>
        </section>
        <section aria-label="Assessment evidence">
          <h3 class="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Evidence from assessments</h3>
          <p v-if="!detail.evidence.length" class="text-xs text-muted-foreground">No evidence yet. Take an assessment to start a validated record.</p>
          <ul v-else class="max-h-40 space-y-1 overflow-y-auto">
            <li v-for="(e, i) in detail.evidence" :key="i" class="flex items-center justify-between gap-2 text-xs"><span class="truncate">{{ dateShort(e.occurred_at) }} · {{ e.kind }}<Badge v-if="!e.graded" class="ml-1">self-submitted</Badge></span><span class="shrink-0 tabular-nums">{{ Math.round(e.score * 100) }}% @ d{{ e.difficulty }}</span></li>
          </ul>
        </section>
        <section v-if="detail.learning_history.length" aria-label="Learning history">
          <h3 class="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Learning history</h3>
          <ul class="space-y-1"><li v-for="h in detail.learning_history" :key="h.session_id" class="flex items-center justify-between gap-2 text-xs">
            <RouterLink :to="{ name: 'history-session', params: { id: h.session_id } }" class="truncate hover:underline">{{ h.title }}</RouterLink>
            <span class="shrink-0 tabular-nums text-muted-foreground">{{ h.completed_sections }}/{{ h.total_sections }} sections</span></li></ul>
        </section>
        <section v-if="detail.related.length" aria-label="Related skills">
          <h3 class="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Related skills</h3>
          <ul class="flex flex-wrap gap-1"><li v-for="u in detail.related" :key="u.skill_id"><button type="button" class="rounded-md bg-muted px-2 py-0.5 text-xs hover:bg-accent" @click="emit('select', u.skill_id)">{{ u.name }}</button></li></ul>
        </section>
      </template>
    </CardContent>
  </Card>
</template>
