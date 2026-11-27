<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { Check, ChevronLeft, ChevronRight, CloudOff } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import Progress from '@/components/ui/Progress.vue'
import Dialog from '@/components/ui/Dialog.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import QuestionInput from '@/features/assessment/QuestionInput.vue'
import AssessmentResultsView from '@/features/assessment/AssessmentResultsView.vue'
import CodeView from '@/features/content/CodeView.vue'
import { assessmentApi } from '@/services/api'
import { ApiError } from '@/services/http'
import { useAssessmentDraft } from '@/stores/assessmentDraft'
import { useAuthStore } from '@/stores/auth'
import { uuid } from '@/utils/id'

const route = useRoute(); const router = useRouter(); const qc = useQueryClient(); const draft = useAssessmentDraft(); const auth = useAuthStore()
const id = computed(() => String(route.params.id))
const detail = useQuery({ queryKey: computed(() => ['assessment', id.value]), queryFn: ({ signal }) => assessmentApi.get(id.value, { signal }) })
const completed = computed(() => detail.data.value?.status === 'completed')
const results = useQuery({ queryKey: computed(() => ['assessment', id.value, 'results']), queryFn: ({ signal }) => assessmentApi.results(id.value, { signal }), enabled: completed })
const qs = computed(() => detail.data.value?.questions ?? [])
const idx = computed(() => Math.min(draft.current, Math.max(0, qs.value.length - 1)))
const q = computed(() => qs.value[idx.value])
const answered = (qid: string) => qid in draft.answers
const answeredCount = computed(() => qs.value.filter((x) => answered(x.question_id)).length)
const confirmOpen = ref(false); const offline = ref(false)
const result = computed(() => results.data.value)

watch([id, () => auth.user?.id], ([a, u]) => { if (u) draft.load(u, a) }, { immediate: true })
// Server-side autosave: every choice is stored by the API shortly after it is made, so a refresh, a crash or another device restores it.
// localStorage (the draft store) stays as an offline fallback only. Answers the server already has fill any gaps locally.
// still being generated: the Assessments page owns the live progress (answers cannot be saved until every question exists)
watch(() => detail.data.value?.generation_status, (g) => { if (g === 'generating') router.replace({ name: 'assessments' }) }, { immediate: true })
watch(() => detail.data.value, (d) => { if (d?.answers && d.status === 'open') draft.merge(d.answers) }, { immediate: true })
type SaveState = 'idle' | 'saving' | 'saved' | 'error'
const saveState = ref<SaveState>('idle'); const dirty = new Set<string>(); const spent: Record<string, number> = {}
let saveTimer: ReturnType<typeof setTimeout> | undefined; let enteredAt = Date.now()
function bookTime() { const q0 = qs.value[idx.value]; if (q0) spent[q0.question_id] = (spent[q0.question_id] ?? 0) + Math.round((Date.now() - enteredAt) / 1000); enteredAt = Date.now() }
async function flush() {
  if (!dirty.size || completed.value) return
  bookTime()
  const ids = [...dirty]; dirty.clear()
  const answers: Record<string, unknown> = {}; const clear: string[] = []; const times: Record<string, number> = {}
  for (const k of ids) { if (k in draft.answers) answers[k] = draft.answers[k]; else clear.push(k); if (spent[k]) times[k] = spent[k] }
  saveState.value = 'saving'
  try { await assessmentApi.saveAnswers(id.value, answers, times, clear); saveState.value = 'saved'; qc.setQueryData(['assessment', id.value], (old: any) => old && { ...old, answers: { ...(old.answers ?? {}), ...answers } }) }
  catch (e) { ids.forEach((k) => dirty.add(k)); saveState.value = 'error'; if (!(e instanceof ApiError && e.status === 409)) saveTimer = setTimeout(flush, 5000) /* retry; answers are also safe locally */ }
}
function onAnswer(qid: string, v: unknown) { draft.setAnswer(qid, v); dirty.add(qid); clearTimeout(saveTimer); saveTimer = setTimeout(flush, 500) }
watch(idx, () => { bookTime() })
const submit = useMutation({
  mutationFn: async () => { clearTimeout(saveTimer); bookTime(); return assessmentApi.submit(id.value, draft.answers) },
  onSuccess: () => { offline.value = false; draft.clear(); toast.success('Assessment submitted'); qc.invalidateQueries({ queryKey: ['assessment'] }); for (const k of ['assessments', 'analytics', 'proficiency', 'gaps', 'skill-history', 'roadmap', 'bank-skills', 'career-graph', 'skill-detail']) qc.invalidateQueries({ queryKey: [k] }) },
  onError: (e) => {
    if (e instanceof ApiError && e.isNetwork) { offline.value = true; toast.error('You appear to be offline. Your answers are saved on this device — retry when reconnected.') }
    else if (e instanceof ApiError && e.status === 409) { qc.invalidateQueries({ queryKey: ['assessment'] }); toast.info(e.message) }
    else toast.error((e as Error).message)
  },
})
const unanswered = computed(() => qs.value.length - answeredCount.value)
function requestSubmit() { confirmOpen.value = true } // a submission is final: always confirm
const onOnline = () => { if (offline.value) toast.info('Back online — you can submit now.'); if (dirty.size) flush() }
function onKey(e: KeyboardEvent) { // Alt+←/→ question navigation
  if (!e.altKey || completed.value) return
  if (e.key === 'ArrowRight') { e.preventDefault(); draft.goto(Math.min(qs.value.length - 1, idx.value + 1)) }
  if (e.key === 'ArrowLeft') { e.preventDefault(); draft.goto(Math.max(0, idx.value - 1)) }
}
onMounted(() => { window.addEventListener('online', onOnline); window.addEventListener('keydown', onKey) })
onBeforeUnmount(() => { window.removeEventListener('online', onOnline); window.removeEventListener('keydown', onKey); clearTimeout(saveTimer); flush() })
const savedLabel = computed(() => saveState.value === 'saving' ? 'Saving…' : saveState.value === 'error' ? 'Could not reach the server — answers are kept on this device and will retry'
  : saveState.value === 'saved' ? 'Answers saved' : draft.savedAt ? `Saved on this device at ${new Date(draft.savedAt).toLocaleTimeString()}` : 'Not saved yet')
const planError = ref<string | null>(null)
const plan = useMutation({
  mutationFn: () => assessmentApi.generatePlan(id.value),
  onMutate: () => { planError.value = null },
  onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['assessment', id.value] }); qc.invalidateQueries({ queryKey: ['content-sessions'] }); router.push({ name: 'history-session', params: { id: r.session_id }, query: { resume: '1' } }) },
  onError: (e) => { planError.value = (e as Error).message },
})
const openPlan = () => { const sid = result.value?.learning_session_id ?? detail.data.value?.learning_session_id; if (sid) router.push({ name: 'history-session', params: { id: sid } }) }
const retake = useMutation({
  mutationFn: () => assessmentApi.create({ skill: detail.data.value!.skill, n_questions: Math.min(20, Math.max(3, qs.value.length)) }, uuid()),
  onSuccess: (a) => { qc.invalidateQueries({ queryKey: ['assessments'] }); router.push({ name: 'assessment-run', params: { id: a.assessment_id } }) },
  onError: (e) => toast.error((e as Error).message),
})
const codeLang = computed(() => (['Python', 'Data Structures and Algorithms'].includes(detail.data.value?.skill ?? '') ? 'python' : ['SQL', 'PostgreSQL'].includes(detail.data.value?.skill ?? '') ? 'sql' : 'text'))
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-4">
    <Skeleton v-if="detail.isPending.value" class="h-72" />
    <ErrorState v-else-if="detail.isError.value" :message="(detail.error.value as Error).message" @retry="detail.refetch()" />
    <template v-else-if="detail.data.value">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div><h1 class="text-2xl font-semibold">{{ detail.data.value.skill }}</h1>
          <p class="text-sm text-muted-foreground">{{ completed ? 'Completed — review below' : `${answeredCount} of ${qs.length} answered` }}</p></div>
        <Badge :tone="completed ? 'success' : 'info'">{{ completed ? 'Completed' : 'In progress' }}</Badge>
      </div>

      <!-- Completed: backend score, proficiency and per-question feedback -->
      <template v-if="completed">
        <Skeleton v-if="results.isPending.value" class="h-40" />
        <ErrorState v-else-if="results.isError.value" :message="(results.error.value as Error).message" @retry="results.refetch()" />
        <AssessmentResultsView v-else-if="result" :result="result" :plan-loading="plan.isPending.value" :plan-error="planError" :retaking="retake.isPending.value" @plan="plan.mutate()" @retake="retake.mutate()" @open-plan="openPlan" />
      </template>

      <!-- In progress -->
      <template v-else-if="q">
        <Progress :value="answeredCount" :max="qs.length" label="Questions answered" />
        <nav aria-label="Question navigation"><ol class="flex flex-wrap gap-1.5">
          <li v-for="(x, i) in qs" :key="x.question_id"><button type="button" :aria-current="i === idx ? 'step' : undefined" :class="['relative grid size-9 place-items-center rounded-md border text-sm', i === idx ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-accent']" @click="draft.goto(i)">
            {{ i + 1 }}<Check v-if="answered(x.question_id)" class="absolute -right-1 -top-1 size-3.5 rounded-full bg-success p-0.5 text-white" aria-hidden="true" /><span class="sr-only">{{ answered(x.question_id) ? ', answered' : ', unanswered' }}</span></button></li>
        </ol></nav>
        <Card class="p-5">
          <div class="mb-3 flex items-center gap-2 text-xs text-muted-foreground"><Badge>{{ q.type.replace('_', ' ') }}</Badge><span>Difficulty {{ q.difficulty }}/5</span><span v-if="q.objective">· {{ q.objective }}</span></div>
          <CodeView v-if="q.code_snippet" :code="q.code_snippet" :lang="codeLang" />
          <QuestionInput :key="q.question_id" :q="q" :model-value="draft.answers[q.question_id]" @update:model-value="onAnswer(q.question_id, $event)" />
        </Card>
        <div v-if="offline" role="alert" class="flex items-center gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm"><CloudOff class="size-4" aria-hidden="true" />Submission failed because the connection was lost. Your answers are safe on this device.
          <Button size="sm" class="ml-auto" :loading="submit.isPending.value" @click="submit.mutate()">Retry submit</Button></div>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-xs text-muted-foreground" aria-live="polite">{{ savedLabel }} · Alt+←/→ to navigate</p>
          <div class="flex gap-2"><Button variant="outline" :disabled="idx === 0" @click="draft.goto(idx - 1)"><ChevronLeft /> Previous</Button>
            <Button v-if="idx < qs.length - 1" variant="outline" @click="draft.goto(idx + 1)">Next <ChevronRight /></Button>
            <Button v-else :loading="submit.isPending.value" @click="requestSubmit">Submit</Button></div>
        </div>
        <div class="flex justify-end"><Button v-if="idx < qs.length - 1" variant="ghost" size="sm" @click="requestSubmit">Submit now</Button></div>
      </template>
    </template>
    <Dialog v-model:open="confirmOpen" :title="unanswered ? 'Submit with unanswered questions?' : 'Submit your assessment?'"
      :description="unanswered ? `${unanswered} question(s) are unanswered and will score zero.` : 'You answered every question. Once submitted, answers cannot be changed.'">
      <div class="flex justify-end gap-2"><Button variant="ghost" @click="confirmOpen = false">Keep working</Button><Button :loading="submit.isPending.value" @click="confirmOpen = false; submit.mutate()">{{ unanswered ? 'Submit anyway' : 'Submit assessment' }}</Button></div>
    </Dialog>
  </div>
</template>
