<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { useRouter } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Unavailable from '@/components/ui/Unavailable.vue'
import Button from '@/components/ui/Button.vue'
import { accuracyOption, describeAccuracy, describeWeekly, weeklyActivityOption } from '@/features/dashboard/chartOptions'
import { describe, progressionOption, readinessTrendOption, retentionOption, roadmapOption } from '@/features/dashboard/analyticsOptions'
import { useSummary } from '@/composables/queries'
import { pct } from '@/utils/format'

const EChart = defineAsyncComponent(() => import('@/features/charts/EChart.vue'))
const router = useRouter()
const { data: s, isPending, isError, error, refetch } = useSummary()
</script>

<template>
  <div class="space-y-5">
    <div><h1 class="text-2xl font-semibold">Analytics</h1><p class="text-sm text-muted-foreground">All charts are computed from your stored assessments, evidence snapshots and roadmaps.</p></div>
    <ErrorState v-if="isError" :message="(error as Error).message" @retry="refetch()" />
    <Skeleton v-else-if="isPending" class="h-96" />
    <div v-else-if="s" class="grid gap-4 lg:grid-cols-2">
      <Card><CardHeader title="Skill progression" description="Proficiency over time, one line per skill" /><CardContent>
        <EmptyState v-if="!s.proficiency_series.length" title="No progression yet" description="Each assessment adds a point to the skill's history." />
        <EChart v-else :option="progressionOption(s)" label="Skill progression chart" :summary="describe.progression(s)" /></CardContent></Card>

      <Card><CardHeader title="Weekly learning activity" /><CardContent><EChart :option="weeklyActivityOption(s)" label="Weekly activity chart" :summary="describeWeekly(s)" /></CardContent></Card>

      <Card><CardHeader title="Assessment accuracy" description="Score of each completed assessment" /><CardContent>
        <EmptyState v-if="!s.accuracy_trend.length" title="No completed assessments" />
        <EChart v-else :option="accuracyOption(s)" label="Assessment accuracy chart" :summary="describeAccuracy(s)" /></CardContent></Card>

      <Card><CardHeader title="Learning consistency" :description="s.consistency.basis" /><CardContent class="space-y-2">
        <dl class="grid grid-cols-3 gap-3 text-sm"><div><dt class="text-xs text-muted-foreground">Current streak</dt><dd class="text-2xl font-semibold tabular-nums">{{ s.consistency.current_streak_days }} <span class="text-sm font-normal">days</span></dd></div>
          <div><dt class="text-xs text-muted-foreground">Active days (last 28)</dt><dd class="text-2xl font-semibold tabular-nums">{{ s.consistency.active_days_last_28 }}</dd></div>
          <div><dt class="text-xs text-muted-foreground">Consistency</dt><dd class="text-2xl font-semibold tabular-nums">{{ pct(s.consistency.ratio) }}</dd></div></dl>
        <Unavailable :reason="s.unavailable.learning_hours" /></CardContent></Card>

      <Card><CardHeader title="Career readiness trend" :description="s.career ? `${s.career.title} — evidence-based, not a hiring prediction` : undefined" /><CardContent>
        <EmptyState v-if="!s.career" title="No career goal" description="Set a goal to track readiness."><Button size="sm" @click="router.push({ name: 'dashboard' })">Go to dashboard</Button></EmptyState>
        <template v-else><EmptyState v-if="s.career.readiness_trend.length < 2" title="Not enough history for a trend" :description="`Readiness is ${pct(s.career.readiness)} now. A trend needs assessments on at least two different days.`" />
          <EChart v-else :option="readinessTrendOption(s)" label="Career readiness trend chart" :summary="describe.readiness(s)" /></template></CardContent></Card>

      <Card><CardHeader title="Knowledge retention" :description="s.unavailable.retention" /><CardContent>
        <EmptyState v-if="!s.retention.length" title="No repeat assessments" description="Assess a skill at least twice to see how your score changed." />
        <EChart v-else :option="retentionOption(s)" label="Score change between assessments" :summary="describe.retention(s)" :height="Math.max(200, s.retention.length * 36 + 60)" /></CardContent></Card>

      <Card><CardHeader title="Roadmap completion" /><CardContent>
        <EmptyState v-if="!s.roadmap_completion.roadmaps.length" title="No roadmap yet" />
        <EChart v-else :option="roadmapOption(s)" label="Roadmap completion chart" :summary="describe.roadmap(s)" /></CardContent></Card>

      <Card><CardHeader title="Strongest and weakest skills" description="By current proficiency; confidence shows how much evidence supports it" /><CardContent class="grid gap-4 sm:grid-cols-2">
        <div v-for="g in [{ t: 'Strongest', rows: s.strongest }, { t: 'Weakest', rows: s.weakest }]" :key="g.t"><h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">{{ g.t }}</h3>
          <p v-if="!g.rows.length" class="text-xs text-muted-foreground">No assessed skills.</p>
          <ol v-else class="space-y-1 text-sm"><li v-for="r in g.rows" :key="r.skill_id" class="flex justify-between"><span>{{ r.skill }}</span><span class="tabular-nums text-muted-foreground">{{ r.proficiency.toFixed(1) }} / 5 · {{ pct(r.confidence) }} conf.</span></li></ol></div></CardContent></Card>
    </div>
  </div>
</template>
