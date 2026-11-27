<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { Bot, Bug, ClipboardCheck, Lightbulb, Map, MessagesSquare, PanelLeft, RefreshCw, Send, SlidersHorizontal, Square } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import Textarea from '@/components/ui/Textarea.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import MarkdownView from '@/features/content/MarkdownView.vue'
import AssistantMessage from '@/features/tutor/AssistantMessage.vue'
import ConversationList from '@/features/tutor/ConversationList.vue'
import ToolCard from '@/features/tutor/ToolCard.vue'
import { STAGES, newMessageId, useTutorStream, type TutorRequest } from '@/features/tutor/useTutorStream'
import { careerApi, tutorApi } from '@/services/api'
import { useUiStore } from '@/stores/ui'
import type { TutorChatOptions, TutorMessage } from '@/types/api'

const MAX = 8000
const route = useRoute(); const qc = useQueryClient(); const ui = useUiStore()
const stream = useTutorStream()
const skill = ref(typeof route.query.skill === 'string' ? route.query.skill : '')
const input = ref(typeof route.query.ask === 'string' ? route.query.ask.slice(0, 500) : '')   // ?ask= prefills the box (the learner presses Send: nothing is sent or generated automatically)
const career = ref('')                       // '' = the learner's own goal
const pending = ref<string | null>(null)     // the user's message for the in-flight turn
const regenerating = ref(false)
const lastRequest = ref<{ body: TutorRequest; regenerate: boolean } | null>(null)
const showList = ref(false)                  // small screens: the history is collapsed
const showOptions = ref(false)               // small screens: the answer settings are collapsed
const scroller = ref<HTMLElement>(); const composer = ref<HTMLElement>()

const roles = useQuery({ queryKey: ['careers'], queryFn: ({ signal }) => careerApi.roles({ signal }), staleTime: 300_000 })
const goals = useQuery({ queryKey: ['goals'], queryFn: ({ signal }) => careerApi.goals({ signal }) })
const myGoal = computed(() => { const slug = goals.data.value?.at(-1)?.role_slug; return roles.data.value?.find((r) => r.slug === slug)?.title })

const active = computed(() => stream.conversationId.value)
const busy = computed(() => stream.state.value === 'connecting' || stream.state.value === 'streaming')
const convQ = useQuery({ queryKey: computed(() => ['tutor', 'conversation', active.value]), queryFn: ({ signal }) => tutorApi.conversation(active.value!, { signal }), enabled: computed(() => !!active.value && !busy.value) })
const live = computed(() => busy.value || pending.value !== null || regenerating.value)
const messages = computed<TutorMessage[]>(() => {
  let all = active.value ? (convQ.data.value?.messages ?? []) : []
  if (!live.value) return all
  if (regenerating.value && all.at(-1)?.role === 'assistant') all = all.slice(0, -1)
  // while the live copy of this turn is on screen, hide its stored copy, so a refetch never shows it twice
  const id = pending.value === null ? null : lastRequest.value?.body.client_message_id
  return all.filter((m) => !(m.role === 'assistant' && m.message_id === stream.messageId.value) && !(id && m.client_message_id === id))
})

const PROMPTS = computed(() => {
  const s = skill.value.trim()
  return [
    { icon: Lightbulb, label: 'Explain a technical concept', text: s ? `Explain ${s} like I'm new to it` : 'Explain ', send: !!s },
    { icon: Bug, label: 'Help me write or debug code', text: 'Help me debug this error:\n', send: false },
    { icon: ClipboardCheck, label: 'Test my knowledge', text: s ? `Quiz me on ${s}, one question at a time` : 'Quiz me on ', send: !!s },
    { icon: Map, label: 'Create a learning roadmap', text: 'Create a roadmap for my career goal', send: true },
    { icon: MessagesSquare, label: 'Prepare me for an interview', text: s ? `Interview me about ${s}` : 'Interview me for my target role', send: true },
  ]
})

// elapsed seconds of the current turn: real waiting time, never a made-up percentage
const elapsed = ref(0); let ticker: ReturnType<typeof setInterval> | undefined
watch(busy, (b) => { clearInterval(ticker); elapsed.value = 0; if (b) ticker = setInterval(() => elapsed.value++, 1000) })
onScopeDispose(() => clearInterval(ticker))

const options = (): TutorChatOptions => ({ skill: skill.value.trim() || undefined, language: ui.tutorLanguage, mode: ui.tutorMode, level: ui.tutorLevel, career_goal: career.value || undefined })

async function run(body: TutorRequest, regenerate: boolean) {
  lastRequest.value = { body, regenerate }
  regenerating.value = regenerate; pending.value = regenerate ? null : (body.message ?? null)
  const path = regenerate ? `/api/v1/tutor/conversations/${active.value}/regenerate` : undefined
  const turn = stream.send(body, { path })
  await scrollEnd()
  await turn
  if (stream.state.value === 'connecting' || stream.state.value === 'streaming') return   // a newer turn has taken over
  // reload what the server stored, then drop the live copy so nothing is shown twice
  await Promise.all([qc.invalidateQueries({ queryKey: ['tutor', 'conversation'] }), qc.invalidateQueries({ queryKey: ['tutor', 'conversations'] })])
  if (active.value) await convQ.refetch()
  const neverSaved = !!stream.error.value && stream.messageId.value === null   // the request failed before the server stored it: keep showing the question
  regenerating.value = false
  if (active.value && !convQ.isError.value && !neverSaved) { pending.value = null; stream.text.value = ''; stream.tools.value = [] }
  await scrollEnd()
}
async function send(text = input.value) {
  const message = text.trim()
  if (!message || busy.value) return
  input.value = ''
  await run({ ...options(), message, client_message_id: newMessageId(), conversation_id: active.value }, false)
}
async function regenerate() { if (!busy.value && active.value) await run(options(), true) }
/** Retry repeats the same request with the same id, so the question is never stored twice. */
async function retry() {
  const r = lastRequest.value
  if (!r || busy.value) return
  await run(r.regenerate ? r.body : { ...r.body, conversation_id: active.value }, r.regenerate)
}
async function pick(p: { text: string; send: boolean }) {
  if (p.send) return send(p.text)
  input.value = p.text; await nextTick(); composer.value?.querySelector('textarea')?.focus()
}

async function scrollEnd() { await nextTick(); scroller.value?.scrollTo({ top: scroller.value.scrollHeight }) }
watch(() => stream.text.value, () => { const el = scroller.value; if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) scrollEnd() })
watch(() => convQ.data.value?.conversation_id, (id) => { if (id) scrollEnd() })
function clear() { pending.value = null; regenerating.value = false; lastRequest.value = null }
function open(id: string) { stream.reset(); clear(); stream.conversationId.value = id; showList.value = false }
function fresh() { stream.reset(); clear(); input.value = ''; showList.value = false }
function removed(id: string) { if (id === active.value) fresh() }
function onKey(e: KeyboardEvent) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() } }
</script>

<template>
  <div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
    <aside aria-label="Conversations" :class="['lg:block lg:max-h-[calc(100vh-8rem)] lg:overflow-y-auto', showList ? 'max-h-[60vh] overflow-y-auto' : 'hidden']">
      <ConversationList :active="active" @select="open" @fresh="fresh" @removed="removed" />
    </aside>

    <Card class="flex h-[calc(100vh-8rem)] min-h-[28rem] min-w-0 flex-col">
      <div class="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-sm">
        <Button class="lg:hidden" size="sm" variant="ghost" :aria-expanded="showList" @click="showList = !showList"><PanelLeft /> History</Button>
        <Bot class="size-4 text-primary" aria-hidden="true" /><h1 class="font-semibold">AI Tutor</h1>
        <Button class="ml-auto lg:hidden" size="sm" variant="ghost" :aria-expanded="showOptions" @click="showOptions = !showOptions"><SlidersHorizontal /> Options</Button>
        <div :class="['ml-auto w-full flex-wrap items-center gap-2 text-xs lg:flex lg:w-auto', showOptions ? 'flex' : 'hidden']">
          <label class="flex items-center gap-1.5">Focus skill<input v-model="skill" class="field h-8 w-28 px-2 text-sm" placeholder="e.g. FastAPI" maxlength="80" /></label>
          <label class="flex items-center gap-1.5">Career goal<select v-model="career" class="field h-8 max-w-40 px-2 text-sm"><option value="">{{ myGoal ? `My goal: ${myGoal}` : 'Not set' }}</option><option v-for="r in roles.data.value" :key="r.slug" :value="r.slug">{{ r.title }}</option></select></label>
          <label class="flex items-center gap-1.5">Level<select v-model="ui.tutorLevel" class="field h-8 px-2 text-sm"><option value="auto">Auto</option><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="advanced">Advanced</option></select></label>
          <label class="flex items-center gap-1.5">Mode<select v-model="ui.tutorMode" class="field h-8 px-2 text-sm"><option value="auto">Auto</option><option value="learn">Learn</option><option value="code">Code</option><option value="practice">Practice</option><option value="interview">Interview</option><option value="system_design">System Design</option><option value="revise">Revise</option></select></label>
          <label class="flex items-center gap-1.5">Language<select v-model="ui.tutorLanguage" class="field h-8 px-2 text-sm"><option value="en">English</option></select></label>
        </div>
      </div>
      <div ref="scroller" class="flex-1 space-y-4 overflow-y-auto p-4" role="log" aria-label="Conversation" aria-live="off" tabindex="0">
        <Skeleton v-if="active && convQ.isLoading.value && !live" class="h-24" />
        <ErrorState v-else-if="active && convQ.isError.value && !live" :message="(convQ.error.value as Error).message" @retry="convQ.refetch()" />
        <div v-else-if="!messages.length && !live && !stream.error.value" class="mx-auto flex max-w-xl flex-col items-center gap-3 py-8 text-center">
          <Bot class="size-10 text-primary" aria-hidden="true" />
          <h2 class="text-xl font-semibold">What would you like to learn today?</h2>
          <p class="text-sm text-muted-foreground">Ask questions, explore concepts, write code, practice and improve your skills.</p>
          <div class="grid w-full gap-2 sm:grid-cols-2"><button v-for="p in PROMPTS" :key="p.label" type="button" class="surface surface-lift flex items-center gap-2 px-3 py-2.5 text-left text-sm" @click="pick(p)"><component :is="p.icon" class="size-4 shrink-0 text-primary" aria-hidden="true" />{{ p.label }}</button></div>
        </div>
        <template v-for="(m, i) in messages" :key="m.message_id ?? i">
          <div v-if="m.role === 'user'" class="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"><p class="whitespace-pre-wrap break-words">{{ m.content }}</p></div>
          <AssistantMessage v-else :message="m" :last="i === messages.length - 1 && !live" :busy="busy" @regenerate="regenerate" @followup="send" />
        </template>
        <template v-if="live">
          <div v-if="pending" class="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"><p class="whitespace-pre-wrap break-words">{{ pending }}</p></div>
          <div v-if="busy || stream.text.value || stream.tools.value.length" class="max-w-[95%] space-y-3 surface px-4 py-3" aria-live="polite" :aria-busy="busy">
            <ToolCard v-for="t in stream.tools.value" :key="t.execution_id" :tool="t" />
            <MarkdownView v-if="stream.text.value" class="tutor-answer" :source="stream.text.value" :streaming="busy" />
            <p v-else-if="busy" class="flex items-center gap-2 text-sm text-muted-foreground" data-testid="stage"><span class="size-2 animate-pulse rounded-full bg-primary" aria-hidden="true" />{{ STAGES[stream.stage.value ?? ''] ?? 'Connecting…' }}<template v-if="elapsed >= 5"> ({{ elapsed }} s)</template></p>
            <p v-if="busy && !stream.text.value && elapsed >= 15" class="text-xs text-muted-foreground">The model has not started answering yet. A locally hosted model can take a minute or more; you can stop at any time.</p>
          </div>
        </template>
        <div v-if="stream.error.value" role="alert" class="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <span class="flex-1">{{ stream.error.value.message }}<template v-if="stream.error.value.partial"> The answer above is incomplete.</template></span>
          <Button v-if="stream.error.value.retryable" size="sm" variant="outline" @click="retry"><RefreshCw /> Retry</Button></div>
      </div>
      <form class="border-t p-3" @submit.prevent="send()">
        <div ref="composer" class="flex items-end gap-2">
          <Textarea v-model="input" class="min-h-10 max-h-48 resize-none [field-sizing:content]" rows="1" placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)" aria-label="Message the tutor" :maxlength="MAX" @keydown="onKey" />
          <Button v-if="busy" variant="outline" aria-label="Stop generating" @click="stream.stop()"><Square /> Stop</Button>
          <Button v-else type="submit" :disabled="!input.trim()" aria-label="Send message"><Send /></Button>
        </div>
        <p v-if="input.length > MAX * 0.8" class="mt-1 text-right text-xs text-muted-foreground">{{ input.length }} / {{ MAX }}</p>
      </form>
    </Card>
  </div>
</template>
