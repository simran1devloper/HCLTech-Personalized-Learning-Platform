<script setup lang="ts">
import { computed } from 'vue'
import MarkdownView from '@/features/content/MarkdownView.vue'
import FlashcardDeck from '@/features/content/FlashcardDeck.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { toDisplayText } from '@/utils/display'

/** Renders one persisted section exactly as the Diagnostic Study page renders a freshly generated one (markdown, math, code, diagrams, flashcards…). */
const props = defineProps<{ sectionKey: string; value: unknown }>()
const rec = computed(() => (props.value && typeof props.value === 'object' ? (props.value as Record<string, any>) : {}))
const concepts = computed(() => (Array.isArray(props.value) ? (props.value as { term: string; definition: string }[]) : []))
const cards = computed(() => (Array.isArray(props.value) ? (props.value as { question: string; answer: string }[]).map((f) => ({ front: f.question, back: f.answer })) : []))
</script>
<template>
  <ul v-if="sectionKey === 'key_concepts'" class="space-y-3">
    <li v-for="c in concepts" :key="toDisplayText(c.term)"><p class="font-medium">{{ toDisplayText(c.term) }}</p><MarkdownView :source="toDisplayText(c.definition)" /></li>
  </ul>
  <template v-else-if="sectionKey === 'flashcards'"><FlashcardDeck v-if="cards.length" :cards="cards" /><EmptyState v-else title="No flashcards in this section" /></template>
  <div v-else-if="sectionKey === 'recommendations'" class="space-y-3 text-sm">
    <div v-if="rec.weak_areas?.length"><h3 class="font-semibold">Weak areas</h3><ul class="list-disc pl-5"><li v-for="w in rec.weak_areas" :key="toDisplayText(w)">{{ toDisplayText(w) }}</li></ul></div>
    <div v-if="rec.next_topics?.length"><h3 class="font-semibold">Next topics</h3><ul class="list-disc pl-5"><li v-for="n in rec.next_topics" :key="toDisplayText(n)">{{ toDisplayText(n) }}</li></ul></div>
    <div v-if="rec.practice_suggestions?.length"><h3 class="font-semibold">Practice</h3><ul class="list-disc pl-5"><li v-for="p in rec.practice_suggestions" :key="toDisplayText(p)">{{ toDisplayText(p) }}</li></ul></div>
  </div>
  <MarkdownView v-else :source="toDisplayText(value)" />
</template>
