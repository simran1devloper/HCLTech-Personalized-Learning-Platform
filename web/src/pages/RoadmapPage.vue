<script setup lang="ts">
import { uuid } from '@/utils/id'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { Ban, CheckCircle2, Circle, Clock, Flag, GitCompare, RefreshCw } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import Progress from '@/components/ui/Progress.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import GoalDialog from '@/features/dashboard/GoalDialog.vue'
import { compareVersions, weekBlocks } from '@/features/roadmap/compare'
import { keys, useGoals, useRoadmapList } from '@/composables/queries'
import { roadmapApi } from '@/services/api'
import type { Milestone, ReplanResult } from '@/types/api'

const qc = useQueryClient(); const router = useRouter()
const list = useRoadmapList(); const goals = useGoals()
const goalOpen = ref(false)
const roadmapId = computed(() => list.data.value?.items[0]?.roadmap_id)
const versionsQ = useQuery({ queryKey: computed(() => keys.versions(roadmapId.value ?? '')), queryFn: ({ signal }) => roadmapApi.versions(roadmapId.value!, { signal }), enabled: computed(() => !!roadmapId.value) })
const versions = computed(() => versionsQ.data.value?.items ?? [])
const picked = ref<number | null>(null); const compareWith = ref<number | null>(null)
watch(versions, (v) => { if (v.length && (picked.value == null || !v.some((x) => x.version === picked.value))) picked.value = v.at(-1)!.version }, { immediate: true })
const current = computed(() => versions.value.find((v) => v.version === picked.value))
const latest = computed(() => versions.value.at(-1))
const diff = computed(() => (current.value && compareWith.value != null ? (() => { const o = versions.value.find((v) => v.version === compareWith.value); return o ? compareVersions(o, current.value!) : null })() : null))
const live = computed(() => current.value?.milestones.filter((m) => m.status !== 'superseded') ?? [])
const done = computed(() => live.value.filter((m) => m.status === 'completed' || m.status === 'skipped_mastered').length)
const blocks = computed(() => (current.value ? weekBlocks(current.value) : []))
const isLatest = computed(() => current.value?.version === latest.value?.version)
const lastReplan = ref<ReplanResult | null>(null)

const refresh = () => { qc.invalidateQueries({ queryKey: ['roadmap'] }); qc.invalidateQueries({ queryKey: ['analytics'] }) }
const generate = useMutation({
  mutationFn: () => { const g = goals.data.value?.at(-1); if (!g) throw new Error('Set a career goal first.'); return roadmapApi.generate({ role_slug: g.role_slug, hours_per_week: g.hours_per_week }, uuid()) },
  onSuccess: () => { toast.success('Roadmap generated'); refresh() }, onError: (e) => { toast.error((e as Error).message); if (!goals.data.value?.length) goalOpen.value = true },
})
const replan = useMutation({
  mutationFn: () => roadmapApi.replan(roadmapId.value!),
  onSuccess: (r) => { lastReplan.value = r; toast[r.changed ? 'success' : 'info'](r.changed ? `Roadmap updated to v${r.roadmap.version}` : 'No changes needed — your roadmap is up to date'); picked.value = r.roadmap.version; refresh() },
  onError: (e) => toast.error((e as Error).message),
})
const complete = useMutation({
  mutationFn: (m: Milestone) => roadmapApi.complete(roadmapId.value!, m.item_id),
  onSuccess: (m) => { toast.success(`Completed: ${m.skill_name}`); refresh() },
  onError: (e) => toast.error((e as Error).message),
})
const statusMeta: Record<string, { label: string; tone: 'success' | 'warning' | 'info' | 'neutral'; icon: any }> = {
  completed: { label: 'Completed', tone: 'success', icon: CheckCircle2 }, skipped_mastered: { label: 'Mastered', tone: 'success', icon: CheckCircle2 },
  blocked: { label: 'Blocked', tone: 'warning', icon: Ban }, in_progress: { label: 'In progress', tone: 'info', icon: Clock }, pending: { label: 'Up next', tone: 'neutral', icon: Circle },
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div><h1 class="text-2xl font-semibold">Learning roadmap</h1>
        <p class="text-sm text-muted-foreground">Ordering, effort and priorities are computed by the backend from your evidence.</p></div>
      <div class="flex gap-2">
        <Button variant="outline" @click="goalOpen = true">Career goal</Button>
        <Button v-if="roadmapId" :loading="replan.isPending.value" @click="replan.mutate()"><RefreshCw /> Re-plan</Button>
      </div>
    </div>

    <Skeleton v-if="list.isPending.value" class="h-64" />
    <ErrorState v-else-if="list.isError.value" :message="(list.error.value as Error).message" @retry="list.refetch()" />
    <EmptyState v-else-if="!roadmapId" :icon="Flag" title="No roadmap yet" description="Choose a career goal and the backend will build an ordered, prerequisite-aware plan from your current proficiency.">
      <div class="flex gap-2"><Button variant="outline" @click="goalOpen = true">Set career goal</Button><Button :loading="generate.isPending.value" :disabled="!goals.data.value?.length" @click="generate.mutate()">Generate roadmap</Button></div>
    </EmptyState>

    <template v-else-if="current">
      <Card class="p-4">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div class="min-w-48 flex-1"><p class="text-xs text-muted-foreground">Goal</p><p class="font-semibold">{{ current.goal.replace(/-/g, ' ') }}</p></div>
          <div class="min-w-56 flex-1"><div class="mb-1 flex justify-between text-xs text-muted-foreground"><span>Progress</span><span>{{ done }} / {{ live.length }} milestones</span></div>
            <Progress :value="done" :max="Math.max(1, live.length)" label="Roadmap completion" /></div>
          <dl class="flex gap-6 text-sm"><div><dt class="text-xs text-muted-foreground">Remaining effort</dt><dd class="font-semibold tabular-nums">{{ current.total_hours }} h</dd></div>
            <div><dt class="text-xs text-muted-foreground">Estimate</dt><dd class="font-semibold tabular-nums">{{ current.estimated_weeks }} wk @ {{ current.hours_per_week }} h</dd></div></dl>
          <label class="text-sm">Version
            <select v-model.number="picked" class="ml-1 h-9 field px-2" aria-label="Roadmap version">
              <option v-for="v in versions" :key="v.version" :value="v.version">v{{ v.version }}{{ v.version === latest?.version ? ' (latest)' : '' }}</option></select></label>
        </div>
        <div v-if="current.change_summary.length" class="mt-3 rounded-md bg-muted/50 p-3 text-sm"><p class="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground">Why this version changed</p>
          <ul class="list-disc pl-5"><li v-for="c in current.change_summary" :key="c">{{ c }}</li></ul></div>
        <p v-if="!isLatest" class="mt-3 text-xs text-warning" role="status">You are viewing an older version (read-only). Actions apply to the latest version.</p>
      </Card>

      <Card v-if="lastReplan?.changed" class="border-primary/40 p-4" role="status">
        <p class="text-sm font-semibold">Latest re-plan</p>
        <ul class="mt-1 flex flex-wrap gap-2 text-xs">
          <Badge v-for="n in lastReplan.diff.added" :key="'a' + n" tone="info">+ {{ n }}</Badge><Badge v-for="n in lastReplan.diff.skipped" :key="'s' + n" tone="success">✓ mastered {{ n }}</Badge>
          <Badge v-for="n in lastReplan.diff.reintroduced" :key="'r' + n" tone="warning">↺ review {{ n }}</Badge><Badge v-for="n in lastReplan.diff.removed" :key="'x' + n">− {{ n }}</Badge>
          <Badge v-for="n in lastReplan.diff.blocked" :key="'b' + n" tone="warning">blocked {{ n }}</Badge><Badge v-for="n in lastReplan.diff.unblocked" :key="'u' + n" tone="success">unblocked {{ n }}</Badge>
        </ul>
      </Card>

      <div class="grid gap-4 xl:grid-cols-[1fr_20rem]">
        <div class="space-y-4">
          <section v-for="b in blocks" :key="b.label" :aria-label="b.label">
            <h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{{ b.label }} <span class="font-normal normal-case">(estimated)</span></h2>
            <ol class="space-y-2">
              <li v-for="m in b.items" :key="m.item_id">
                <Card class="p-4">
                  <div class="flex flex-wrap items-start justify-between gap-2">
                    <div class="min-w-0"><p class="flex items-center gap-2 font-medium"><component :is="statusMeta[m.status]?.icon ?? Circle" class="size-4" aria-hidden="true" />{{ m.order }}. {{ m.skill_name }}
                      <Badge :tone="statusMeta[m.status]?.tone">{{ statusMeta[m.status]?.label ?? m.status }}</Badge><Badge v-if="m.kind === 'review'" tone="warning">Review</Badge></p>
                      <p class="mt-1 text-xs text-muted-foreground">{{ m.from_level.toFixed(1) }} → {{ m.target_level.toFixed(1) }} · ~{{ m.estimated_hours }} h · checkpoint: {{ m.checkpoint }}</p></div>
                    <div class="flex gap-2">
                      <Button size="sm" variant="outline" @click="router.push({ name: 'assessments', query: { skill: m.skill_name } })">Assess</Button>
                      <Button v-if="isLatest && m.status !== 'completed' && m.status !== 'skipped_mastered'" size="sm" :disabled="m.status === 'blocked'" :loading="complete.isPending.value && complete.variables.value?.item_id === m.item_id" @click="complete.mutate(m)">Mark complete</Button>
                    </div>
                  </div>
                  <ul class="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground"><li v-for="o in m.objectives" :key="o">{{ o }}</li></ul>
                  <p class="mt-2 text-xs text-muted-foreground">Completion: proficiency ≥ {{ m.completion_criteria.min_proficiency }} with confidence ≥ {{ Math.round(m.completion_criteria.min_confidence * 100) }}% (verified by the backend).</p>
                  <p v-if="m.blocked_by.length" class="mt-1 text-xs text-warning">Blocked by {{ m.blocked_by.length }} unmet prerequisite{{ m.blocked_by.length > 1 ? 's' : '' }}.</p>
                </Card>
              </li>
            </ol>
          </section>
        </div>

        <Card class="h-fit">
          <CardHeader title="Compare versions" description="What differs between two stored versions"><template #action><GitCompare class="size-4 text-muted-foreground" aria-hidden="true" /></template></CardHeader>
          <CardContent class="space-y-3 text-sm">
            <p v-if="versions.length < 2" class="text-xs text-muted-foreground">Only one version exists. Re-plan after new assessments to create another.</p>
            <template v-else>
              <label class="block text-xs">Compare v{{ current.version }} against
                <select v-model.number="compareWith" class="mt-1 h-9 w-full field px-2 text-sm"><option :value="null">Select a version…</option>
                  <option v-for="v in versions.filter((x) => x.version !== current!.version)" :key="v.version" :value="v.version">v{{ v.version }}</option></select></label>
              <div v-if="diff" class="space-y-2" aria-live="polite">
                <p class="text-xs text-muted-foreground">Remaining effort {{ diff.hoursDelta > 0 ? '+' : '' }}{{ diff.hoursDelta }} h</p>
                <div v-if="diff.added.length"><p class="text-xs font-semibold">Added</p><ul class="text-xs"><li v-for="m in diff.added" :key="m.item_id">+ {{ m.skill_name }}</li></ul></div>
                <div v-if="diff.removed.length"><p class="text-xs font-semibold">Removed</p><ul class="text-xs"><li v-for="m in diff.removed" :key="m.item_id">− {{ m.skill_name }}</li></ul></div>
                <div v-if="diff.statusChanged.length"><p class="text-xs font-semibold">Status changed</p><ul class="text-xs"><li v-for="c in diff.statusChanged" :key="c.skill">{{ c.skill }}: {{ c.from }} → {{ c.to }}</li></ul></div>
                <p v-if="!diff.added.length && !diff.removed.length && !diff.statusChanged.length" class="text-xs text-muted-foreground">No differences.</p>
              </div>
            </template>
          </CardContent>
        </Card>
      </div>
    </template>
    <GoalDialog v-model:open="goalOpen" />
  </div>
</template>
