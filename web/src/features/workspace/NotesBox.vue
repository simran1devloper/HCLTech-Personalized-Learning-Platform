<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { NotebookPen } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'

/** Per-section notes, saved automatically (debounced) to the learner's own session. Rendered as plain text only. */
const props = defineProps<{ sectionKey: string; saved: string }>()
const emit = defineEmits<{ save: [note: string] }>()
const text = ref(props.saved); const status = ref<'idle' | 'saving' | 'saved'>('idle')
let t: ReturnType<typeof setTimeout> | undefined
watch(() => [props.sectionKey, props.saved] as const, ([, s]) => { if (!t) text.value = s }, { immediate: true })
function onInput() { status.value = 'saving'; clearTimeout(t); t = setTimeout(flush, 700) }
function flush() { clearTimeout(t); t = undefined; if (status.value === 'saving') { emit('save', text.value); status.value = 'saved' } }
onBeforeUnmount(flush)
</script>
<template>
  <Card class="p-4"><label class="flex items-center gap-2 text-sm font-semibold" :for="`notes-${sectionKey}`"><NotebookPen class="size-4 text-primary" aria-hidden="true" />My notes</label>
    <textarea :id="`notes-${sectionKey}`" v-model="text" maxlength="5000" rows="3" placeholder="Write what you want to remember about this section…" class="mt-2 w-full field p-2 text-sm" @input="onInput" @blur="flush"></textarea>
    <p class="mt-1 text-xs text-muted-foreground" aria-live="polite">{{ status === 'saving' ? 'Saving…' : status === 'saved' ? 'Saved' : 'Notes are private to you.' }}</p></Card>
</template>
