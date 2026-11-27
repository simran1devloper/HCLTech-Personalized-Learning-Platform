<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMutation } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { Activity, ArrowRight, CheckCircle2, Clock, Flame, Gauge, Layers, Sparkles, Target } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import Progress from '@/components/ui/Progress.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import StatTile from '@/features/dashboard/StatTile.vue'
import GoalDialog from '@/features/dashboard/GoalDialog.vue'
import { accuracyOption, describeAccuracy, describeProficiency, describeWeekly, proficiencyBarsOption, weeklyActivityOption } from '@/features/dashboard/chartOptions'
import { useAssessmentList, useSummary } from '@/composables/queries'
import { analyticsApi } from '@/services/api'
import { useAuthStore } from '@/stores/auth'
import { pct, relativeTime } from '@/utils/format'

const EChart = defineAsyncComponent(() => import('@/features/charts/EChart.vue')) // chart lib loads on demand
const auth = useAuthStore(); const router = useRouter()
const { data: s, isPending, isError, error, refetch } = useSummary()
const recent = useAssessmentList(5)
const goalOpen = ref(false)
const narrative = useMutation({ mutationFn: () => analyticsApi.narrate(), onError: (e) => toast.error((e as Error).message) })
const career = computed(() => s.value?.career ?? null)
const first = computed(() => auth.user?.name.split(' ')[0] ?? '')
const hasActivity = computed(() => (s.value?.assessments_completed ?? 0) > 0)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div><h1 class="text-2xl font-semibold">Welcome, {{ first }}</h1><p class="text-sm text-muted-foreground">Everything below is computed from your assessments and roadmap.</p></div>
      <Button variant="outline" @click="goalOpen = true"><Target /> {{ career ? 'Change career goal' : 'Set career goal' }}</Button>
    </div>

    <ErrorState v-if="isError" :message="(error as Error).message" @retry="refetch()" />
    <template v-else>
      <section aria-label="Key metrics" class="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Career readiness" :icon="Gauge" :loading="isPending" :value="career ? pct(career.readiness) : undefined" :unavailable="career ? undefined : 'set a career goal'" :hint="career ? `${career.title} · evidence confidence ${pct(career.evidence_confidence)}` : undefined" />
        <StatTile label="Skills assessed" :icon="Layers" :loading="isPending" :value="String(s?.skills_assessed ?? 0)" />
        <StatTile label="Learning hours" :icon="Clock" :loading="isPending" :unavailable="s?.unavailable.learning_hours ?? 'not tracked'" />
        <StatTile label="Assessment streak" :icon="Flame" :loading="isPending" :value="`${s?.consistency.current_streak_days ?? 0} d`" :hint="s?.consistency.basis" />
        <StatTile label="Assessments done" :icon="CheckCircle2" :loading="isPending" :value="String(s?.assessments_completed ?? 0)" />
        <StatTile label="Roadmap complete" :icon="Activity" :loading="isPending" :value="s?.roadmap_completion.ratio == null ? undefined : pct(s.roadmap_completion.ratio)" :unavailable="s && s.roadmap_completion.ratio == null ? 'no roadmap generated yet' : undefined" :hint="s ? `${s.roadmap_completion.done} of ${s.roadmap_completion.total} milestones` : undefined" />
      </section>

      <div class="grid gap-4 lg:grid-cols-3">
        <Card class="lg:col-span-2">
          <CardHeader title="Weekly activity" description="Completed assessments and questions answered per week" />
          <CardContent><Skeleton v-if="isPending" class="h-64" /><EChart v-else-if="s" :option="weeklyActivityOption(s)" label="Weekly activity chart" :summary="describeWeekly(s)" /></CardContent>
        </Card>

        <Card>
          <CardHeader title="Recommended next action" />
          <CardContent class="space-y-3">
            <Skeleton v-if="isPending" class="h-24" />
            <template v-else-if="career?.recommended_next">
              <p class="text-sm">Highest-priority skill with all prerequisites met:</p>
              <p class="text-lg font-semibold">{{ career.recommended_next.name }}</p>
              <div class="flex flex-wrap gap-2">
                <Button size="sm" @click="router.push({ name: 'assessments', query: { skill: career.recommended_next.name } })">Assess this skill <ArrowRight /></Button>
                <Button size="sm" variant="outline" @click="router.push({ name: 'tutor', query: { skill: career.recommended_next.name } })">Ask the tutor</Button>
              </div>
            </template>
            <EmptyState v-else title="No recommendation yet" description="Set a career goal to get a prioritised next skill."><Button size="sm" @click="goalOpen = true">Set career goal</Button></EmptyState>
          </CardContent>
        </Card>

        <Card class="lg:col-span-2">
          <CardHeader title="Skill proficiency" description="Latest proficiency per assessed skill (0–5), from your evidence" />
          <CardContent>
            <Skeleton v-if="isPending" class="h-64" />
            <EmptyState v-else-if="!s?.proficiency_series.length" title="No assessed skills yet" description="Complete an assessment to see proficiency."><Button size="sm" @click="router.push({ name: 'assessments' })">Take an assessment</Button></EmptyState>
            <EChart v-else :option="proficiencyBarsOption(s)" label="Skill proficiency chart" :summary="describeProficiency(s)" :height="Math.max(200, s.proficiency_series.length * 38 + 60)" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="Skill gap summary" :description="career ? `Against ${career.title}` : undefined" />
          <CardContent class="space-y-3">
            <Skeleton v-if="isPending" class="h-32" />
            <EmptyState v-else-if="!career" title="No goal set" description="Gaps are measured against a role's required skill levels." />
            <template v-else>
              <p class="text-xs text-muted-foreground">{{ career.gap_summary.open_gaps }} open gaps · {{ career.gap_summary.blocked }} blocked by prerequisites</p>
              <ul class="space-y-3">
                <li v-for="g in career.gap_summary.top" :key="g.skill_id">
                  <div class="mb-1 flex items-center justify-between text-sm"><span class="font-medium">{{ g.name }}</span><span class="tabular-nums text-xs text-muted-foreground">{{ g.current.toFixed(1) }} / {{ g.target.toFixed(1) }}</span></div>
                  <Progress :value="g.current" :max="g.target" :label="`${g.name}: ${g.current.toFixed(1)} of ${g.target.toFixed(1)}`" />
                  <Badge v-if="g.blocked" tone="warning" class="mt-1">Blocked by prerequisites</Badge>
                </li>
              </ul>
            </template>
          </CardContent>
        </Card>

        <Card class="lg:col-span-2">
          <CardHeader title="Assessment performance" description="Score of each completed assessment" />
          <CardContent>
            <Skeleton v-if="isPending" class="h-56" />
            <EmptyState v-else-if="!hasActivity" title="No completed assessments" description="Scores appear here after you submit an assessment." />
            <EChart v-else-if="s" :option="accuracyOption(s)" label="Assessment score trend" :summary="describeAccuracy(s)" :height="220" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="Recent learning activity" />
          <CardContent>
            <Skeleton v-if="recent.isPending.value" class="h-32" />
            <ErrorState v-else-if="recent.isError.value" :message="(recent.error.value as Error).message" @retry="recent.refetch()" />
            <EmptyState v-else-if="!recent.data.value?.items.length" title="Nothing yet" description="Your assessments will be listed here." />
            <ul v-else class="divide-y">
              <li v-for="a in recent.data.value.items" :key="a.assessment_id" class="flex items-center justify-between gap-2 py-2 text-sm">
                <RouterLink :to="{ name: 'assessment-run', params: { id: a.assessment_id } }" class="font-medium hover:underline">{{ a.skill }}</RouterLink>
                <span class="text-xs text-muted-foreground">{{ a.status === 'completed' ? pct(a.score) + ' · ' : 'in progress · ' }}{{ relativeTime(a.completed_at ?? a.started_at) }}</span>
              </li>
            </ul>
          </CardContent>
        </Card>

        <Card class="lg:col-span-3">
          <CardHeader title="Learning insights" :description="s?.insights_basis">
            <template #action><Button size="sm" variant="outline" :loading="narrative.isPending.value" :disabled="!hasActivity" @click="narrative.mutate()"><Sparkles /> AI summary</Button></template>
          </CardHeader>
          <CardContent class="space-y-3">
            <Skeleton v-if="isPending" class="h-16" />
            <ul v-else class="space-y-2 text-sm"><li v-for="i in s?.insights" :key="i.text" class="flex gap-2"><span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />{{ i.text }}</li></ul>
            <div v-if="narrative.data.value" class="rounded-md border bg-muted/40 p-3 text-sm" aria-live="polite">
              <p class="mb-1 text-xs text-muted-foreground">Generated by {{ narrative.data.value.model }} from the figures above — verify before relying on it.</p>
              <p class="whitespace-pre-wrap">{{ narrative.data.value.text }}</p>
            </div>
            <p v-if="!hasActivity && !isPending" class="text-xs text-muted-foreground">The AI summary needs at least one completed assessment.</p>
          </CardContent>
        </Card>
      </div>
    </template>
    <GoalDialog v-model:open="goalOpen" />
  </div>
</template>
