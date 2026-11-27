<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, Info, Lightbulb, OctagonAlert, StickyNote } from 'lucide-vue-next'
import type { Block } from '@/schemas/blocks'
import MarkdownView from './MarkdownView.vue'
import CodeView from './CodeView.vue'
import MermaidView from './MermaidView.vue'
import MathBlock from './MathBlock.vue'
import ChartBlock from './ChartBlock.vue'
import FlashcardDeck from './FlashcardDeck.vue'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import { ref } from 'vue'

/** Renders ONE already-validated block. Text goes through the safe markdown pipeline or is rendered as plain text;
 *  there is no raw-HTML path. */
const props = defineProps<{ block: Block }>()
const b = computed(() => props.block as any)
const tag = computed(() => `h${Math.min(6, Math.max(3, (b.value.level ?? 2) + 1))}`)   // content headings sit under the section's h2: levels 1-2 -> h3, 3 -> h4, 4 -> h5 (no skipped levels)
const callouts = { info: { icon: Info, label: 'Info', cls: 'border-primary/40 bg-accent/40' }, tip: { icon: Lightbulb, label: 'Tip', cls: 'border-success/40 bg-success/10' },
  warning: { icon: AlertTriangle, label: 'Warning', cls: 'border-warning/50 bg-warning/10' }, danger: { icon: OctagonAlert, label: 'Danger', cls: 'border-destructive/40 bg-destructive/10' },
  note: { icon: StickyNote, label: 'Note', cls: 'border-border bg-muted/40' } } as const
const reveal = ref(false); const done = ref(new Set<number>())
const toggleStep = (i: number) => { const n = new Set(done.value); n.has(i) ? n.delete(i) : n.add(i); done.value = n }
</script>
<template>
  <component :is="tag" v-if="b.type === 'heading'" class="mt-4 font-semibold">{{ b.text }}</component>
  <MarkdownView v-else-if="b.type === 'text'" :source="b.markdown" />
  <component :is="b.ordered ? 'ol' : 'ul'" v-else-if="b.type === 'list'" :class="['space-y-1 pl-5 text-sm', b.ordered ? 'list-decimal' : 'list-disc']"><li v-for="(it, i) in b.items" :key="i">{{ it }}</li></component>
  <div v-else-if="b.type === 'table'" class="overflow-x-auto"><table class="prose-lite w-full"><caption v-if="b.caption" class="mb-1 text-left text-xs text-muted-foreground">{{ b.caption }}</caption>
    <thead><tr><th v-for="h in b.headers" :key="h" scope="col">{{ h }}</th></tr></thead><tbody><tr v-for="(r, ri) in b.rows" :key="ri"><td v-for="(c, ci) in r" :key="ci">{{ c }}</td></tr></tbody></table></div>
  <aside v-else-if="b.type === 'callout'" :class="['my-3 flex gap-2 rounded-md border p-3 text-sm', callouts[b.variant as keyof typeof callouts].cls]" role="note" :aria-label="callouts[b.variant as keyof typeof callouts].label">
    <component :is="callouts[b.variant as keyof typeof callouts].icon" class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
    <p><strong>{{ callouts[b.variant as keyof typeof callouts].label }}:</strong> {{ b.text }}</p></aside>
  <MermaidView v-else-if="b.type === 'mermaid'" :code="b.source" :caption="b.caption" />
  <MathBlock v-else-if="b.type === 'math'" :latex="b.latex" :display="b.display" :variables="b.variables" :title="b.title" />
  <ChartBlock v-else-if="b.type === 'chart'" :block="b" />
  <figure v-else-if="b.type === 'image'" class="my-3"><img :src="b.url" :alt="b.alt" loading="lazy" referrerpolicy="no-referrer" class="max-w-full rounded-md border" /><figcaption v-if="b.caption" class="mt-1 text-xs text-muted-foreground">{{ b.caption }}</figcaption></figure>
  <CodeView v-else-if="b.type === 'code'" :code="b.code" :lang="b.language" :caption="b.caption || b.filename" />
  <Card v-else-if="b.type === 'algorithm'" class="p-4 text-sm"><p class="font-semibold">{{ b.name }}</p>
    <p v-if="b.time_complexity || b.space_complexity" class="mt-1 text-xs text-muted-foreground"><template v-if="b.time_complexity">Time: {{ b.time_complexity }}</template><template v-if="b.time_complexity && b.space_complexity"> · </template><template v-if="b.space_complexity">Space: {{ b.space_complexity }}</template></p>
    <ol class="mt-2 list-decimal space-y-1 pl-5"><li v-for="(s, i) in b.steps" :key="i">{{ s }}</li></ol><CodeView v-if="b.pseudocode" :code="b.pseudocode" lang="text" /></Card>
  <Card v-else-if="b.type === 'quiz'" class="p-4 text-sm"><p class="font-medium">Knowledge check</p><p class="text-muted-foreground">{{ b.question_ids.length }} question(s) from the assessment bank.</p>
    <RouterLink :to="{ name: 'assessments' }"><Button size="sm" variant="outline" class="mt-2">Open assessments</Button></RouterLink></Card>
  <FlashcardDeck v-else-if="b.type === 'flashcard'" :cards="[{ front: b.front, back: b.back }]" />
  <FlashcardDeck v-else-if="b.type === 'flashcards'" :cards="b.cards" />
  <Card v-else-if="b.type === 'exercise'" class="p-4 text-sm"><p class="font-semibold">Exercise<template v-if="b.difficulty"> · difficulty {{ b.difficulty }}/5</template></p><MarkdownView :source="b.prompt" />
    <details v-for="(h, hi) in (b.hints as string[])" :key="hi" class="mt-2"><summary class="cursor-pointer text-xs text-primary">Hint {{ hi + 1 }}</summary><p class="mt-1">{{ h }}</p></details>
    <div v-if="b.solution_outline" class="mt-2"><Button v-if="!reveal" size="sm" variant="outline" @click="reveal = true">Show solution outline</Button><MarkdownView v-else :source="b.solution_outline" /></div></Card>
  <p v-else-if="b.type === 'citation'" :id="`cite-${b.label.replace(/\D/g, '')}`" class="rounded-md border-l-2 bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
    <span class="font-medium text-foreground">{{ b.label }}</span> {{ b.source_title }}<template v-if="b.quote"> — “{{ b.quote }}”</template> ·
    <a :href="b.url" target="_blank" rel="noopener noreferrer nofollow" class="text-primary underline">source</a></p>
  <Card v-else-if="b.type === 'case_study'" class="p-4 text-sm"><p class="font-semibold">{{ b.title }}</p><MarkdownView :source="b.scenario" />
    <ol v-if="b.questions.length" class="mt-2 list-decimal space-y-1 pl-5"><li v-for="q in b.questions" :key="q">{{ q }}</li></ol></Card>
  <Card v-else-if="b.type === 'tutorial'" class="p-4 text-sm"><p class="mb-2 font-semibold">Step-by-step</p>
    <ol class="space-y-2"><li v-for="(s, si) in (b.steps as string[])" :key="si"><label class="flex items-start gap-2"><input type="checkbox" class="mt-1 accent-[var(--primary)]" :checked="done.has(si)" @change="toggleStep(si)" /><span :class="done.has(si) && 'text-muted-foreground line-through'">{{ s }}</span></label></li></ol></Card>
</template>
