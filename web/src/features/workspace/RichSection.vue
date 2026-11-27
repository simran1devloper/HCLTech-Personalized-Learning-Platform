<script setup lang="ts">
import { computed } from 'vue'
import { CheckCircle2, Lightbulb, Target } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import MarkdownView from '@/features/content/MarkdownView.vue'
import FlashcardDeck from '@/features/content/FlashcardDeck.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { toDisplayText } from '@/utils/display'
import { conceptsOf, objectivesFrom, takeawaysFrom } from './derive'

/** Renders one generated section with the right component: definition cards for concepts, a deck for flashcards, structured cards for recommendations,
 *  safe markdown (code via Shiki, diagrams via Mermaid, math via KaTeX, callouts) for prose. Objectives and takeaways are derived from the content itself. */
const props = defineProps<{ sectionKey: string; value: unknown; concepts: unknown; streaming?: boolean }>()
const text = computed(() => (typeof props.value === 'string' ? props.value : toDisplayText(props.value)))
const cards = computed(() => conceptsOf(props.value))
const objectives = computed(() => (props.sectionKey === 'summary' ? objectivesFrom(conceptsOf(props.concepts)) : []))
const takeaways = computed(() => (props.sectionKey === 'summary' && typeof props.value === 'string' ? takeawaysFrom(props.value) : []))
const flash = computed(() => (Array.isArray(props.value) ? (props.value as { question: string; answer: string }[]).map((f) => ({ front: toDisplayText(f.question), back: toDisplayText(f.answer) })) : []))
const rec = computed(() => (props.value && typeof props.value === 'object' && !Array.isArray(props.value) ? (props.value as Record<string, any[]>) : {}))
const list = (v: unknown) => (Array.isArray(v) ? v.map((x) => toDisplayText(x)).filter(Boolean) : [])
</script>
<template>
  <div class="space-y-4">
    <Card v-if="objectives.length" class="border-primary/20 bg-accent/40 p-4" aria-label="Learning objectives"><h3 class="mb-2 flex items-center gap-2 text-sm font-semibold"><Target class="size-4 text-primary" aria-hidden="true" />Learning Objectives</h3>
      <ul class="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2"><li v-for="o in objectives" :key="o" class="flex items-start gap-2"><CheckCircle2 class="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />{{ o }}</li></ul></Card>

    <div v-if="sectionKey === 'key_concepts'" class="grid gap-3 sm:grid-cols-2" aria-label="Key concepts">
      <Card v-for="c in cards" :key="c.term" class="p-4"><h3 class="text-sm font-semibold">{{ c.term }}</h3><MarkdownView :source="c.definition" class="mt-1" /></Card>
      <EmptyState v-if="!cards.length" title="No concepts were extracted" class="sm:col-span-2" /></div>
    <template v-else-if="sectionKey === 'flashcards'"><FlashcardDeck v-if="flash.length" :cards="flash" /><EmptyState v-else title="No flashcards were generated" /></template>
    <div v-else-if="sectionKey === 'recommendations'" class="grid gap-3 sm:grid-cols-3">
      <Card v-if="list(rec.weak_areas).length" class="p-4"><h3 class="text-sm font-semibold">Weak areas</h3><ul class="mt-1 list-disc space-y-1 pl-5 text-sm"><li v-for="w in list(rec.weak_areas)" :key="w">{{ w }}</li></ul></Card>
      <Card v-if="list(rec.next_topics).length" class="p-4"><h3 class="text-sm font-semibold">Next topics</h3><ul class="mt-1 list-disc space-y-1 pl-5 text-sm"><li v-for="n in list(rec.next_topics)" :key="n">{{ n }}</li></ul></Card>
      <Card v-if="list(rec.practice_suggestions).length" class="p-4"><h3 class="text-sm font-semibold">Practice</h3><ul class="mt-1 list-disc space-y-1 pl-5 text-sm"><li v-for="p in list(rec.practice_suggestions)" :key="p">{{ p }}</li></ul></Card></div>
    <MarkdownView v-else :source="text" :streaming="streaming" />

    <Card v-if="takeaways.length && !streaming" class="border-primary/20 bg-accent/40 p-4" aria-label="Key takeaways"><h3 class="mb-2 flex items-center gap-2 text-sm font-semibold"><Lightbulb class="size-4 text-primary" aria-hidden="true" />Key Takeaways</h3>
      <ul class="list-disc space-y-1 pl-5 text-sm"><li v-for="t in takeaways" :key="t">{{ t }}</li></ul></Card>
  </div>
</template>
