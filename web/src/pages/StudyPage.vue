<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, Check, Clock, History, Loader2, RefreshCw, Square } from 'lucide-vue-next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import Progress from '@/components/ui/Progress.vue'
import CodeView from '@/features/content/CodeView.vue'
import QuestionInput from '@/features/assessment/QuestionInput.vue'
import { SOURCE_LABEL, normalizeTopic, stepsFor, topicError, type StartStep } from '@/features/study/diagnostic'
import { useDebounced } from '@/composables/useDebounced'
import { assessmentApi, diagnosticApi, legacyApi } from '@/services/api'
import { ApiError } from '@/services/http'
import { uuid } from '@/utils/id'
import type { DiagnosticPlan, DiagnosticResult, DiagnosticSession } from '@/types/api'
import { relativeTime } from '@/utils/format'

/** Diagnostic Study: topic → quiz (database-first; AI only for questions the bank lacks) → results → study material → flashcards.
 *  The quiz is a persistent session on the server, so a refresh or another device resumes it, and answers never reach the browser before submission. */
const qc = useQueryClient(); const route = useRoute(); const router = useRouter()
const topic = ref('')
const session = ref<DiagnosticSession | null>(null); const answers = ref<Record<string, unknown>>({}); const result = ref<DiagnosticResult | null>(null)
const step = computed(() => (result.value ? 'results' : session.value ? 'quiz' : 'topic'))

// ---- topic input: free text with registry suggestions (suggestions never restrict what can be typed)
const typed = useDebounced(topic, 200)
const suggestions = useQuery({ queryKey: computed(() => ['topic-suggest', normalizeTopic(typed.value)]), queryFn: ({ signal }) => diagnosticApi.suggest(normalizeTopic(typed.value), { signal }),
  enabled: computed(() => step.value === 'topic' && normalizeTopic(typed.value).length >= 2), staleTime: 60_000 })
const invalid = computed(() => (topic.value ? topicError(topic.value) : null))

// ---- start: the real decision is made by the backend; the steps shown mirror it (no AI wording unless the model will actually be used)
const elapsed = ref(0); let tick: ReturnType<typeof setInterval> | undefined
const starting = ref(false); const plan = ref<DiagnosticPlan | null>(null); const startError = ref<string | null>(null); const stepIdx = ref(0)
const steps = computed<StartStep[]>(() => stepsFor(plan.value))
async function generateQuiz() {
  if (starting.value || topicError(topic.value)) return
  starting.value = true; startError.value = null; plan.value = null; stepIdx.value = 0; elapsed.value = 0
  clearInterval(tick); tick = setInterval(() => elapsed.value++, 1000)
  const body = { topic: normalizeTopic(topic.value), question_count: 10 }
  const key = uuid()
  try {
    try { plan.value = await diagnosticApi.check(body) } catch (e) { if (e instanceof ApiError && e.status === 422) throw e /* otherwise: just start */ }
    // The plan is the backend's own decision, and starting executes exactly that plan, so the steps are shown as soon as it is known (no timers):
    // database-only plans show retrieval steps; plans that use the model show the generation step while the request is really generating.
    stepIdx.value = plan.value ? (plan.value.will_call_ai ? steps.value.length - 2 : steps.value.length - 1) : 0
    open(await diagnosticApi.start(body, key))
  } catch (e) {
    startError.value = e instanceof ApiError && e.isNetwork ? 'Could not reach the server. Check your connection and try again.' : (e as Error).message || 'The quiz could not be started.'
  } finally { starting.value = false; clearInterval(tick) }
}
function open(s: DiagnosticSession) {
  session.value = s; answers.value = { ...(s.answers ?? {}) }; result.value = s.result ?? null
  if (s.note) toast.info(s.note)
  router.replace({ query: { ...route.query, session: s.session_id } })
  qc.invalidateQueries({ queryKey: ['diagnostic-sessions'] })
}
async function resume(id: string) {
  starting.value = true; startError.value = null
  try { open(await diagnosticApi.session(id)) } catch (e) { startError.value = (e as Error).message; router.replace({ query: {} }) } finally { starting.value = false }
}
onBeforeUnmount(() => clearInterval(tick))
onMounted(() => {
  if (typeof route.query.session === 'string') resume(route.query.session)
  else if (typeof route.query.topic === 'string') topic.value = route.query.topic.slice(0, 80)   // from the Skill Explorer: prefilled, never auto-started (starting may use the model)
})
/** Context handed over by the Skill Explorer (?skill=<id>&topic=<name>&career=<slug>&intent=learn|revise). */
const explorer = computed(() => (typeof route.query.skill === 'string' ? { skill: route.query.skill, career: typeof route.query.career === 'string' ? route.query.career : undefined, revise: route.query.intent === 'revise' } : null))   // refresh / shared link: restore, never regenerate

// ---- quiz: answers autosave to the server shortly after each change
let saveTimer: ReturnType<typeof setTimeout> | undefined; const dirty = new Set<string>(); const saving = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
async function flush() {
  const sid = session.value?.session_id; if (!sid || !dirty.size || result.value) return
  const ids = [...dirty]; dirty.clear()
  const send: Record<string, unknown> = {}; const clear: string[] = []
  for (const k of ids) (k in answers.value ? (send[k] = answers.value[k]) : clear.push(k))
  saving.value = 'saving'
  try { await assessmentApi.saveAnswers(sid, send, undefined, clear); saving.value = 'saved' } catch { ids.forEach((k) => dirty.add(k)); saving.value = 'error'; saveTimer = setTimeout(flush, 5000) }
}
function setAnswer(qid: string, v: unknown) {
  const empty = v === undefined || v === '' || (Array.isArray(v) && !v.length); const next = { ...answers.value }
  if (empty) delete next[qid]; else next[qid] = v
  answers.value = next; dirty.add(qid); clearTimeout(saveTimer); saveTimer = setTimeout(flush, 500)
}
const answered = computed(() => Object.keys(answers.value).length)
const total = computed(() => session.value?.questions.length ?? 0)
const codeLang = computed(() => (/python|\bpy\b/i.test(session.value?.topic ?? '') ? 'python' : /sql/i.test(session.value?.topic ?? '') ? 'sql' : 'text'))
const submit = useMutation({
  mutationFn: async () => { clearTimeout(saveTimer); return diagnosticApi.submit(session.value!.session_id, answers.value) },
  onSuccess: (s) => { result.value = s.result ?? null; if (session.value) session.value = { ...session.value, status: 'completed' }; qc.invalidateQueries({ queryKey: ['diagnostic-sessions'] }) },
  onError: (e) => toast.error((e as Error).message),
})

// ---- study material: the AI learning workspace owns generation, progress and reading state (and survives a refresh)
function startStudy() {
  if (!result.value || !session.value) return
  router.push({ name: 'study-learn', query: { topic: session.value.topic || normalizeTopic(topic.value), level: result.value.level, score: String(result.value.score_10), diag: session.value.session_id } })
}
const sessions = useQuery({ queryKey: ['diagnostic-sessions'], queryFn: ({ signal }) => diagnosticApi.sessions({ signal }) })
const history = useQuery({ queryKey: ['legacy-history'], queryFn: ({ signal }) => legacyApi.history({ signal }) })
const reset = () => { clearTimeout(saveTimer); flush(); session.value = null; result.value = null; answers.value = {}; plan.value = null; router.replace({ query: {} }) }
watch(() => route.query.session, (v) => { if (!v && session.value) { session.value = null; result.value = null } })
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-5">
    <div><h1 class="text-2xl font-semibold">Diagnostic study</h1><p class="text-sm text-muted-foreground">Pick any topic: a quick diagnostic quiz, then study material matched to your level.</p></div>

    <p v-if="explorer" class="flex flex-wrap items-center gap-x-2 rounded-lg border bg-accent/50 px-3 py-2 text-sm" data-test="explorer-context">
      <span>{{ explorer.revise ? 'Revising' : 'Learning' }} a skill from the Skill Explorer<template v-if="explorer.career"> for the <strong>{{ explorer.career.replace(/-/g, ' ') }}</strong> career</template>.</span>
      <RouterLink :to="{ name: 'skills', query: { skill: explorer.skill, ...(explorer.career ? { role: explorer.career } : {}) } }" class="text-primary hover:underline">Back to Skill Explorer</RouterLink></p>

    <Card v-if="step === 'topic'" class="space-y-4 p-5">
      <form class="flex flex-wrap gap-2" @submit.prevent="generateQuiz">
        <div class="min-w-56 flex-1"><Input v-model="topic" aria-label="Topic" list="topic-suggestions" autocomplete="off" placeholder="e.g. Python decorators, REST API design…" maxlength="80" class="w-full" :disabled="starting" :aria-invalid="!!invalid" />
          <datalist id="topic-suggestions"><option v-for="s in suggestions.data.value?.items ?? []" :key="s.skill_id" :value="s.name">{{ s.question_count ? `${s.question_count} saved questions` : 'new topic' }}</option></datalist>
          <p v-if="invalid" class="mt-1 text-xs text-destructive" role="status">{{ invalid }}</p></div>
        <Button type="submit" :loading="starting" :disabled="!!topicError(topic)">Generate quiz</Button>
      </form>
      <ol v-if="starting" class="space-y-1.5 rounded-md border bg-muted/30 p-3 text-sm" role="status" aria-live="polite" aria-label="Quiz preparation progress">
        <li v-for="(st, i) in steps.slice(0, stepIdx + 1)" :key="st.id" class="flex items-center gap-2" :data-step="st.id">
          <Loader2 v-if="i === Math.min(stepIdx, steps.length - 1) && starting" class="size-4 animate-spin text-primary" aria-hidden="true" /><Check v-else class="size-4 text-success" aria-hidden="true" />{{ st.label }}
          <span v-if="i === Math.min(stepIdx, steps.length - 1) && elapsed >= 3 && plan?.will_call_ai" class="text-xs text-muted-foreground tabular-nums">({{ elapsed }}s)</span></li>
        <li v-if="plan?.will_call_ai && elapsed >= 20" class="pt-1 text-xs text-muted-foreground">A local AI model can take a few minutes. Each question is saved as soon as it is checked, so nothing is lost if this takes a while. You can start with a smaller set if only part is ready.</li></ol>
      <div v-if="startError && !starting" role="alert" class="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
        <AlertTriangle class="size-4 text-destructive" aria-hidden="true" /><span class="flex-1">{{ startError }}</span><Button size="sm" variant="outline" @click="generateQuiz"><RefreshCw /> Try again</Button></div>
      <div v-if="sessions.data.value?.items.length"><h2 class="mb-2 text-xs font-semibold uppercase text-muted-foreground">Previous sessions</h2>
        <ul class="divide-y text-sm" aria-label="Previous diagnostic sessions"><li v-for="h in sessions.data.value.items" :key="h.session_id" class="flex flex-wrap items-center justify-between gap-2 py-2">
          <span class="flex min-w-0 items-center gap-2"><button type="button" class="truncate font-medium hover:underline" @click="topic = h.topic">{{ h.topic }}</button>
            <Badge :tone="h.status === 'completed' ? 'success' : 'warning'">{{ h.status === 'completed' ? 'Completed' : 'In progress' }}</Badge><Badge v-if="h.generation_source">{{ SOURCE_LABEL[h.generation_source] }}</Badge></span>
          <span class="flex items-center gap-3 text-xs text-muted-foreground"><span>{{ h.status === 'completed' ? `${Math.round((h.score_pct ?? 0) / 10)}/10 · ${h.level}` : `${h.answered_questions}/${h.question_count} answered` }} · {{ relativeTime(h.started_at) }}</span>
            <Button size="sm" variant="outline" @click="resume(h.session_id)">{{ h.status === 'completed' ? 'Review' : 'Resume' }}<span class="sr-only"> {{ h.topic }}</span></Button></span></li></ul></div>
      <div v-if="history.data.value?.history.length"><h2 class="mb-2 text-xs font-semibold uppercase text-muted-foreground">Earlier study sessions</h2>
        <ul class="divide-y text-sm"><li v-for="h in history.data.value.history" :key="h.id" class="flex items-center justify-between py-2"><button type="button" class="font-medium hover:underline" @click="topic = h.topic">{{ h.topic }}</button><span class="text-xs text-muted-foreground">{{ h.score }}/10 · {{ h.level }} · {{ relativeTime(h.created_at) }}</span></li></ul></div>
    </Card>

    <template v-else-if="step === 'quiz' && session">
      <div class="flex flex-wrap items-center justify-between gap-2"><h2 class="text-lg font-semibold">{{ session.topic }}</h2>
        <span class="flex items-center gap-2 text-xs text-muted-foreground"><Badge v-if="session.generation_source">{{ SOURCE_LABEL[session.generation_source] }}</Badge>{{ session.question_count }} questions</span></div>
      <p v-if="session.note" role="status" class="rounded-md border border-warning bg-warning/10 p-2 text-sm">{{ session.note }}</p>
      <Progress :value="answered" :max="total" label="Questions answered" />
      <ol class="space-y-4"><li v-for="(q, qi) in session.questions" :key="q.question_id"><Card class="p-4">
        <p class="mb-2 text-xs text-muted-foreground">Question {{ qi + 1 }} of {{ total }}</p>
        <CodeView v-if="q.code_snippet" :code="q.code_snippet" :lang="codeLang" />
        <QuestionInput :q="q" :model-value="answers[q.question_id]" @update:model-value="setAnswer(q.question_id, $event)" /></Card></li></ol>
      <div class="flex flex-wrap items-center justify-between gap-2"><Button variant="ghost" @click="reset">Leave quiz</Button>
        <span class="text-xs text-muted-foreground" aria-live="polite">{{ saving === 'saving' ? 'Saving…' : saving === 'saved' ? 'Answers saved' : saving === 'error' ? 'Could not save — retrying' : '' }}</span>
        <Button :loading="submit.isPending.value" :disabled="answered < total" @click="submit.mutate()">Submit quiz ({{ answered }}/{{ total }})</Button></div>
    </template>

    <template v-else-if="step === 'results' && result">
      <Card class="p-5" role="status"><p class="text-xs text-muted-foreground">Result (graded by the server)</p><p class="text-3xl font-semibold tabular-nums">{{ result.score }} / {{ result.total }} <Badge class="ml-2" :tone="result.level === 'Expert' ? 'success' : result.level === 'Intermediate' ? 'info' : 'warning'">{{ result.level }}</Badge></p></Card>
      <ol class="space-y-2"><li v-for="(b, i) in result.breakdown" :key="i"><Card class="p-3 text-sm"><p class="font-medium">{{ i + 1 }}. {{ b.question }}</p>
        <p class="mt-1 text-xs"><Badge :tone="b.correct ? 'success' : 'danger'">{{ b.correct ? 'Correct' : 'Incorrect' }}</Badge> Your answer: {{ b.user_answer ?? '—' }}<template v-if="!b.correct"> · Correct: {{ b.correct_answer }}</template></p>
        <p v-if="b.explanation" class="mt-1 text-xs text-muted-foreground">{{ b.explanation }}</p></Card></li></ol>
      <div class="flex justify-between"><Button variant="ghost" @click="reset">New topic</Button><Button @click="startStudy">Generate study material</Button></div>
    </template>

  </div>
</template>
