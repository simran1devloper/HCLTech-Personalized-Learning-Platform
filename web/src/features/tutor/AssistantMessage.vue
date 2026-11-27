<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { toast } from 'vue-sonner'
import { Copy, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import MarkdownView from '@/features/content/MarkdownView.vue'
import ToolCard from './ToolCard.vue'
import { tutorApi } from '@/services/api'
import type { TutorMessage } from '@/types/api'

const props = defineProps<{ message: TutorMessage; last?: boolean; busy?: boolean }>()
const emit = defineEmits<{ regenerate: []; followup: [text: string] }>()
const rating = ref(props.message.feedback ?? null)
watch(() => props.message.feedback, (v) => (rating.value = v ?? null))
const note = computed(() => ({ cancelled: 'Stopped before the answer finished.', interrupted: 'The connection was lost; this answer is incomplete.',
  failed: props.message.content ? 'Generation failed; this answer is incomplete.' : 'No answer was generated.', streaming: 'This answer is still being generated.' } as Record<string, string>)[props.message.status ?? 'complete'])
const followups = computed(() => (props.last && !props.busy ? (props.message.meta?.followups ?? []) : []))

async function copy() { try { await navigator.clipboard.writeText(props.message.content); toast.success('Response copied') } catch { toast.error('Copy failed') } }
async function rate(v: 'up' | 'down') {
  const next = rating.value === v ? null : v, before = rating.value
  rating.value = next
  try { await tutorApi.feedback(props.message.message_id!, next) } catch (e) { rating.value = before; toast.error((e as Error).message) }
}
</script>

<template>
  <div class="max-w-[95%] space-y-2">
    <div class="surface space-y-3 px-4 py-3">
      <ToolCard v-for="t in message.tools" :key="t.execution_id" :tool="t" />
      <MarkdownView v-if="message.content" class="tutor-answer" :source="message.content" />
      <p v-if="note" class="text-xs text-muted-foreground" data-testid="reply-note">{{ note }}</p>
      <p v-if="message.meta?.truncated" class="text-xs text-muted-foreground">The answer reached its length limit. <button type="button" class="text-primary underline" @click="emit('followup', 'Continue from where you stopped.')">Continue</button></p>
      <p v-if="message.meta?.context_unavailable" class="text-xs text-muted-foreground">Your learning data could not be loaded, so this answer is not personalised.</p>
      <p v-if="message.meta?.sources?.length" class="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">Used your:
        <RouterLink v-for="s in message.meta.sources" :key="s.kind" :to="s.link" class="rounded-md border px-1.5 py-0.5 hover:bg-accent">{{ s.label }}</RouterLink></p>
      <div class="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
        <button v-if="message.content" type="button" class="inline-flex items-center gap-1 rounded px-1.5 py-1 hover:bg-accent hover:text-foreground" @click="copy"><Copy class="size-3.5" aria-hidden="true" />Copy explanation</button>
        <button v-if="last && !busy" type="button" class="inline-flex items-center gap-1 rounded px-1.5 py-1 hover:bg-accent hover:text-foreground" @click="emit('regenerate')"><RefreshCw class="size-3.5" aria-hidden="true" />Regenerate</button>
        <template v-if="message.message_id && message.content">
          <button type="button" :aria-pressed="rating === 'up'" aria-label="Good response" :class="['rounded p-1.5 hover:bg-accent hover:text-foreground', rating === 'up' && 'bg-accent text-primary']" @click="rate('up')"><ThumbsUp class="size-3.5" aria-hidden="true" /></button>
          <button type="button" :aria-pressed="rating === 'down'" aria-label="Bad response" :class="['rounded p-1.5 hover:bg-accent hover:text-foreground', rating === 'down' && 'bg-accent text-destructive']" @click="rate('down')"><ThumbsDown class="size-3.5" aria-hidden="true" /></button>
        </template>
        <span v-if="message.meta?.model && message.meta.model !== 'rules'" class="ml-auto">{{ message.meta.model }}</span>
      </div>
    </div>
    <div v-if="followups.length" class="flex flex-wrap gap-1.5" aria-label="Suggested follow-up questions" role="group">
      <button v-for="f in followups" :key="f" type="button" class="rounded-full border bg-card px-3 py-1 text-xs hover:bg-accent" @click="emit('followup', f)">{{ f }}</button>
    </div>
  </div>
</template>
