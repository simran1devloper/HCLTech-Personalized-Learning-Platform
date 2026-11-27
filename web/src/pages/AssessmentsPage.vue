<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { AlertTriangle, Check, CircleDashed, ClipboardCheck, Clock, Loader2, Search, Sparkles, Square } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import { useDebounced } from '@/composables/useDebounced'
import { assessmentApi, questionBankApi } from '@/services/api'
import { STAGE_LABELS, useAssessmentGeneration } from '@/features/assessment/useAssessmentGeneration'
import { ApiError } from '@/services/http'
import { uuid } from '@/utils/id'
import { pct, relativeTime } from '@/utils/format'
import type { Assessment, BankSkill } from '@/types/api'

const route = useRoute(); const router = useRouter(); const qc = useQueryClient()
const COUNTS = [3, 5, 10, 15, 20]
const find = ref(''); const findQ = useDebounced(find, 200)
// Skills come with how many validated questions the bank holds, the learner's previous proficiency and a recommendation flag (metadata only: no model call).
const skills = useQuery({ queryKey: ['bank-skills'], queryFn: ({ signal }) => questionBankApi.skills(undefined, { signal }), staleTime: 60_000 })
const all = computed(() => skills.data.value?.items ?? [])
const shown = computed(() => { const t = findQ.value.trim().toLowerCase(); return t ? all.value.filter((s) => s.name.toLowerCase().includes(t) || s.category.toLowerCase().includes(t)) : all.value })
const skillId = ref(''); const n = ref(10)
const wanted = typeof route.query.skill === 'string' ? route.query.skill.toLowerCase() : ''
watch(all, (items) => {
  if (skillId.value || !items.length) return
  skillId.value = (wanted && items.find((s) => s.name.toLowerCase() === wanted || s.skill_id === wanted)?.skill_id) || items.find((s) => s.name === 'Python')?.skill_id || items[0].skill_id
}, { immediate: true })
watch(shown, (items) => { if (items.length && !items.some((s) => s.skill_id === skillId.value)) skillId.value = items[0].skill_id }) // never leave a skill selected that the list no longer shows
const skill = computed<BankSkill | undefined>(() => all.value.find((s) => s.skill_id === skillId.value))
const recommended = computed(() => all.value.filter((s) => s.recommended))
const minutes = computed(() => Math.max(1, Math.round((n.value * (skill.value?.est_seconds_per_question || 60)) / 60)))
const availability = computed(() => {
  const s = skill.value; if (!s) return null
  if (s.question_count === 0) return { tone: 'warning' as const, text: 'No saved questions yet — they will be generated when you start. With a local AI model this can take a few minutes; finished questions are saved as they go.' }
  if (s.question_count < n.value) return { tone: 'info' as const, text: `${s.question_count} questions are ready now; you will get those and more are being prepared.` }
  return { tone: 'success' as const, text: `Ready: ${s.question_count} validated questions across ${s.concepts} concept areas.` }
})

const offset = ref(0); const limit = 10
const list = useQuery({ queryKey: computed(() => ['assessments', { offset: offset.value, limit }]), queryFn: ({ signal }) => assessmentApi.list({ offset: offset.value, limit }, { signal }), placeholderData: (p) => p })
const idemKey = ref(uuid()) // double-click / retry safe: the same key always maps to the same assessment, even after a server restart
const startError = ref<string | null>(null)
const startNotice = ref<string | null>(null)
const finished = ref<{ id: string; status: 'failed' | 'cancelled'; completed: number; requested: number } | null>(null) // a generation that ended before the requested count
const skillName = ref('')

/** Open the quiz: prime the cache from the persisted attempt, then go to the run page. */
async function openAttempt(id: string) {
  try {
    const d = await assessmentApi.get(id)
    qc.setQueryData(['assessment', id], d)
  } catch { /* the run page loads it itself */ }
  qc.invalidateQueries({ queryKey: ['assessments'] }); qc.invalidateQueries({ queryKey: ['bank-skills'] })
  router.push({ name: 'assessment-run', params: { id } })
}
// Live generation: SSE from the backend (persisted, replayable). Progress is saved questions / requested, never a timer.
const gen = useAssessmentGeneration({
  onTerminal: (s) => {
    qc.invalidateQueries({ queryKey: ['bank-skills'] })
    if (s.status === 'completed') { idemKey.value = uuid(); void openAttempt(s.assessment_id); return }
    finished.value = { id: s.assessment_id, status: s.status as 'failed' | 'cancelled', completed: s.completed, requested: s.requested }
    if (s.status === 'failed') startError.value = gen.message.value || (s.completed ? `Only ${s.completed} of ${s.requested} questions could be prepared.` : 'Questions could not be generated. Please try again.')
    else startNotice.value = s.completed ? `Generation stopped. ${s.completed} of ${s.requested} questions were saved.` : 'Generation stopped.'
    idemKey.value = uuid()
  },
})
const busy = computed(() => gen.active.value) // one generation at a time: the Start button and Enter key are disabled meanwhile
// A refresh (or a second tab) finds the generation that is still running and re-attaches to it.
onMounted(async () => {
  try {
    const { active } = await assessmentApi.activeGeneration()
    if (active && !gen.active.value) { skillName.value = active.skill ?? ''; if (active.skill_id) skillId.value = active.skill_id; n.value = active.requested ?? n.value; gen.attach(active) }
  } catch { /* not signed in yet / offline: the Start button still works */ }
})
const stageText = computed(() => gen.stopping.value ? 'Stopping…' : gen.reconnecting.value ? 'Connection lost — reconnecting…' : STAGE_LABELS[gen.stage.value] || 'Starting…')
const start = useMutation({
  mutationFn: () => {
    if (!skill.value) throw new Error('Choose a skill first.')
    if (!COUNTS.includes(n.value)) throw new Error('Choose how many questions you want.')
    return assessmentApi.create({ skill: skill.value.name, n_questions: n.value }, idemKey.value)
  },
  onMutate: () => { startError.value = null; startNotice.value = null; finished.value = null; skillName.value = skill.value?.name ?? '' },
  onSuccess: (a: Assessment) => {
    if (a.status === 'generating') { gen.attach(a); return } // 202: the attempt exists; questions arrive over SSE
    idemKey.value = uuid()
    if (a.status && a.status !== 'open') { gen.attach(a); return }
    // the bank had everything: render the quiz instantly from the response; the page still re-validates against the server
    qc.setQueryData(['assessment', a.assessment_id], { assessment_id: a.assessment_id, skill_id: a.skill_id, skill: a.skill ?? skill.value?.name ?? '', status: 'open', started_at: new Date().toISOString(),
      questions: a.questions, total_questions: a.total_questions ?? a.questions.length, answered_questions: 0, answers: {} })
    qc.invalidateQueries({ queryKey: ['assessments'] }); qc.invalidateQueries({ queryKey: ['bank-skills'] })
    if (a.note) toast.info(a.note)
    router.push({ name: 'assessment-run', params: { id: a.assessment_id } })
  },
  onError: (e) => {
    // never silent: an inline, persistent message (a toast alone disappears) that says what happened and what to do
    startError.value = e instanceof ApiError && e.isNetwork ? 'Could not reach the server. Check your connection and try again.' : (e as Error).message || 'The assessment could not be started.'
    idemKey.value = uuid()
  },
})
const total = computed(() => list.data.value?.total ?? 0)
const submitStart = () => { if (!start.isPending.value && !busy.value) start.mutate() } // ignores a second click while a request or a generation is running
</script>

<template>
  <div class="space-y-5">
    <div><h1 class="text-2xl font-semibold">Assessments</h1><p class="text-sm text-muted-foreground">Adaptive checks per skill. Your proficiency is recalculated only when you submit.</p></div>
    <Card>
      <CardHeader title="Start an assessment" />
      <CardContent>
        <form class="space-y-4" @submit.prevent="submitStart">
          <div v-if="skills.isPending.value" role="status" aria-label="Loading skills"><Skeleton class="h-9 w-full max-w-xl" /></div>
          <ErrorState v-else-if="skills.isError.value" :message="(skills.error.value as Error).message" @retry="skills.refetch()" />
          <template v-else>
            <div v-if="recommended.length" class="flex flex-wrap items-center gap-2"><span class="inline-flex items-center gap-1 text-xs text-muted-foreground"><Sparkles class="size-3.5" aria-hidden="true" />Recommended</span>
              <button v-for="r in recommended" :key="r.skill_id" type="button" :aria-pressed="r.skill_id === skillId" :class="['rounded-full border px-3 py-1 text-xs', r.skill_id === skillId ? 'border-primary bg-accent font-medium' : 'hover:bg-accent']" @click="skillId = r.skill_id; find = ''">{{ r.name }}</button></div>
            <div class="flex flex-wrap items-end gap-3">
              <label class="text-sm">Find a skill<span class="relative mt-1 block"><Search class="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden="true" /><Input v-model="find" type="search" placeholder="Search skills…" maxlength="60" class="w-56 pl-8" /></span></label>
              <label class="text-sm">Skill<select v-model="skillId" class="mt-1 block h-9 w-64 field px-2"><option v-for="s in shown" :key="s.skill_id" :value="s.skill_id">{{ s.name }}</option></select></label>
              <label class="text-sm">Questions<select v-model.number="n" class="mt-1 block h-9 w-24 field px-2"><option v-for="x in COUNTS" :key="x" :value="x">{{ x }}</option></select></label>
              <Button type="submit" :loading="start.isPending.value" :disabled="!skill || busy">Start</Button>
            </div>
            <p v-if="!shown.length" class="text-xs text-muted-foreground" role="status">No skills match “{{ find }}”.</p>
            <div v-if="skill" class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span class="inline-flex items-center gap-1"><Clock class="size-3.5" aria-hidden="true" />About {{ minutes }} min</span>
              <Badge v-if="skill.proficiency != null" tone="info">Your level: {{ skill.proficiency.toFixed(1) }} / 5</Badge><Badge v-else>Not assessed yet</Badge>
              <Badge v-if="availability" :tone="availability.tone">{{ availability.text }}</Badge>
            </div>
          </template>
          <div v-if="start.isPending.value && !busy" class="rounded-md border bg-muted/30 p-3 text-sm" role="status" aria-live="polite">Starting {{ skillName }}…</div>
          <div v-if="busy || (finished && gen.requested.value)" class="space-y-3 rounded-md border bg-muted/30 p-3" role="status" aria-live="polite" data-testid="generation-panel">
            <div class="flex items-center justify-between gap-3 text-sm">
              <span class="font-medium">{{ skillName }}: <span data-testid="generation-count" class="tabular-nums">{{ gen.completed.value }} of {{ gen.requested.value }}</span> questions ready<span class="text-muted-foreground"> · {{ gen.progress.value }}%</span></span>
              <Button v-if="busy" size="sm" variant="outline" type="button" :loading="gen.stopping.value" @click="gen.cancel()"><Square class="size-3.5" aria-hidden="true" />Stop</Button>
            </div>
            <div class="h-2 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Questions saved" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="gen.progress.value">
              <div class="h-full rounded-full bg-primary transition-all duration-300" :style="{ width: gen.progress.value + '%' }" />
            </div>
            <p v-if="busy" class="text-xs text-muted-foreground" data-testid="generation-stage">{{ stageText }}<span v-if="gen.streamed.value"> · the model has written {{ gen.streamed.value }} question{{ gen.streamed.value === 1 ? '' : 's' }} in this step (checking them now; not counted until saved)</span></p>
            <ol class="flex flex-wrap gap-1.5" aria-label="Question status">
              <li v-for="q in gen.slots.value" :key="q.index" :title="q.concept || undefined" :data-state="q.state"
                :class="['inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs', q.state === 'saved' ? 'border-primary/40 bg-accent' : 'text-muted-foreground']">
                <Check v-if="q.state === 'saved'" class="size-3" aria-hidden="true" /><Loader2 v-else-if="q.state === 'working'" class="size-3 animate-spin" aria-hidden="true" /><CircleDashed v-else class="size-3" aria-hidden="true" />
                <span>Q{{ q.index + 1 }}</span><span class="sr-only">{{ q.state === 'saved' ? 'saved' : q.state === 'working' ? 'in progress' : 'waiting' }}</span>
              </li>
            </ol>
            <p v-if="gen.rejected.value || gen.duplicates.value" class="text-xs text-muted-foreground">{{ gen.rejected.value }} invalid and {{ gen.duplicates.value }} duplicate question(s) were discarded and are being replaced.</p>
            <p v-if="busy" class="text-xs text-muted-foreground">Questions already in the bank are used first; only the missing ones are generated. Each is saved as soon as it is checked, so stopping keeps them.</p>
            <div v-else-if="finished && finished.completed" class="flex flex-wrap items-center gap-2 text-sm">
              <span>{{ finished.completed }} of {{ finished.requested }} questions are saved.</span>
              <Button size="sm" type="button" @click="openAttempt(finished.id)">Continue with {{ finished.completed }}</Button>
              <Button size="sm" variant="outline" type="button" @click="finished = null; submitStart()">Generate again</Button>
            </div>
          </div>
          <p v-if="startNotice" role="status" class="rounded-md border bg-muted/30 p-3 text-sm">{{ startNotice }}</p>
          <div v-if="startError" role="alert" class="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm"><AlertTriangle class="size-4 text-destructive" aria-hidden="true" /><span class="flex-1">{{ startError }}</span>
            <Button v-if="!finished?.completed" size="sm" variant="outline" type="button" @click="submitStart">Try again</Button></div>
        </form>
        <p class="mt-3 text-xs text-muted-foreground">Questions come from a saved, validated question bank, and recently seen ones are avoided. A short quiz samples only part of a skill, so treat results as a guide.</p>
      </CardContent>
    </Card>

    <Card>
      <CardHeader title="History" :description="total ? `${total} assessments` : undefined" />
      <CardContent>
        <Skeleton v-if="list.isPending.value" class="h-40" />
        <ErrorState v-else-if="list.isError.value" :message="(list.error.value as Error).message" @retry="list.refetch()" />
        <EmptyState v-else-if="!list.data.value?.items.length" :icon="ClipboardCheck" title="No assessments yet" description="Start one above to build evidence for a skill." />
        <template v-else>
          <div class="overflow-x-auto"><table class="w-full text-sm"><caption class="sr-only">Assessment history</caption>
            <thead><tr class="border-b text-left text-xs text-muted-foreground"><th scope="col" class="py-2 pr-3 font-medium">Skill</th><th scope="col" class="px-3 font-medium">Status</th><th scope="col" class="px-3 font-medium">Score</th><th scope="col" class="px-3 font-medium">Questions</th><th scope="col" class="px-3 font-medium">When</th><th scope="col" class="pl-3"><span class="sr-only">Open</span></th></tr></thead>
            <tbody><tr v-for="a in list.data.value.items" :key="a.assessment_id" class="border-b last:border-0">
              <th scope="row" class="py-2 pr-3 text-left font-medium">{{ a.skill }}</th>
              <td class="px-3"><Badge :tone="a.status === 'completed' ? 'success' : 'warning'">{{ a.status === 'completed' ? 'Completed' : 'In progress' }}</Badge></td>
              <td class="px-3 tabular-nums">{{ a.status === 'completed' ? pct(a.score) : '—' }}</td><td class="px-3 tabular-nums">{{ a.n_questions }}</td>
              <td class="px-3 text-muted-foreground">{{ relativeTime(a.completed_at ?? a.started_at) }}</td>
              <td class="pl-3 text-right"><RouterLink :to="{ name: 'assessment-run', params: { id: a.assessment_id } }" class="text-primary hover:underline">{{ a.status === 'completed' ? 'Review' : 'Resume' }}<span class="sr-only"> {{ a.skill }}</span></RouterLink></td></tr></tbody></table></div>
          <div v-if="total > limit" class="mt-3 flex items-center justify-between text-xs text-muted-foreground"><span>{{ offset + 1 }}–{{ Math.min(offset + limit, total) }} of {{ total }}</span>
            <span class="flex gap-2"><Button size="sm" variant="outline" :disabled="offset === 0" @click="offset = Math.max(0, offset - limit)">Previous</Button><Button size="sm" variant="outline" :disabled="offset + limit >= total" @click="offset += limit">Next</Button></span></div>
        </template>
      </CardContent>
    </Card>
  </div>
</template>
