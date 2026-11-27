<script setup lang="ts">
import { BarChart3, Briefcase, CheckCircle2, Target } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Progress from '@/components/ui/Progress.vue'
import type { StudyProfile } from '@/types/api'
import type { Progress as P } from './derive'

/** Topic, career goal, level and learning progress. The career goal comes from the learner's own saved goal: nothing here assumes a particular career. */
defineProps<{ topic: string; level: string; profile: StudyProfile | null; progress: P }>()
</script>
<template>
  <section aria-label="Learning overview" class="grid gap-3 lg:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)_12rem]">
    <Card class="grid gap-3 p-3 text-sm sm:grid-cols-[1fr_1.4fr_1fr]">
      <div class="flex min-w-0 items-center gap-2"><span class="grid size-8 shrink-0 place-items-center rounded-md bg-accent text-primary"><Target class="size-4" aria-hidden="true" /></span>
        <div class="min-w-0"><p class="whitespace-nowrap text-xs text-muted-foreground">Current Topic</p><p class="truncate font-medium" data-test="topic" :title="topic">{{ topic }}</p></div></div>
      <div class="flex min-w-0 items-center gap-2"><span class="grid size-8 shrink-0 place-items-center rounded-md bg-accent text-primary"><Briefcase class="size-4" aria-hidden="true" /></span>
        <div class="min-w-0"><p class="whitespace-nowrap text-xs text-muted-foreground">Career Goal</p>
          <p v-if="profile?.career" class="truncate font-medium" data-test="career" :title="profile.career.title">{{ profile.career.title }}</p>
          <RouterLink v-else :to="{ name: 'dashboard' }" class="text-primary hover:underline">Set a goal</RouterLink></div></div>
      <div class="flex min-w-0 items-center gap-2"><span class="grid size-8 shrink-0 place-items-center rounded-md bg-accent text-primary"><BarChart3 class="size-4" aria-hidden="true" /></span>
        <div class="min-w-0"><p class="whitespace-nowrap text-xs text-muted-foreground">Skill Level</p><p class="truncate font-medium" data-test="level">{{ level }}</p></div></div>
    </Card>
    <Card class="grid grid-cols-2 gap-4 p-3 text-sm">
      <div><div class="flex justify-between text-xs"><span class="text-muted-foreground">Overall Progress</span><span class="font-medium tabular-nums" data-test="overall-pct">{{ progress.understoodPct }}%</span></div>
        <Progress :value="progress.understood" :max="Math.max(1, progress.total)" label="Overall progress (sections understood)" class="mt-1.5" /></div>
      <div><div class="flex justify-between text-xs"><span class="text-muted-foreground">Reading Progress</span><span class="font-medium tabular-nums" data-test="reading-pct">{{ progress.readingPct }}%</span></div>
        <Progress :value="progress.opened" :max="Math.max(1, progress.total)" label="Reading progress (sections opened)" class="mt-1.5 [&>div]:bg-success" /></div>
    </Card>
    <Card class="flex items-center gap-3 border-success/40 bg-success/10 p-3"><CheckCircle2 class="size-7 shrink-0 text-success" aria-hidden="true" />
      <div><p class="text-lg font-semibold leading-none tabular-nums" data-test="completed">{{ progress.understood }} / {{ progress.total }}</p><p class="text-xs text-muted-foreground">Topics Completed</p></div></Card>
    <p v-if="profile?.weak?.length" class="text-xs text-muted-foreground lg:col-span-3">Extra depth on your weak areas: <span class="font-medium text-foreground">{{ profile.weak.join(', ') }}</span><template v-if="profile.strong.length"> · Not re-teaching: {{ profile.strong.join(', ') }}</template>.</p>
  </section>
</template>
