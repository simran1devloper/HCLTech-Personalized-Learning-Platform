<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { toSegments } from './markdown'
import CodeView from './CodeView.vue'
import MermaidView from './MermaidView.vue'

/** Safe markdown: raw HTML is escaped (never rendered), code via Shiki, diagrams via Mermaid (strict), math via KaTeX.
 *  Segments are keyed so unchanged blocks are not re-rendered while a response streams in. */
const props = defineProps<{ source: string; streaming?: boolean }>()
const root = ref<HTMLElement>()
const segments = computed(() => toSegments(props.source))

async function hydrateMath() {
  await nextTick()
  const nodes = root.value?.querySelectorAll<HTMLElement>('span.math:not([data-done])')
  if (!nodes?.length) return
  const { default: katex } = await import('katex')
  await import('katex/dist/katex.min.css')
  nodes.forEach((el) => {
    try { el.innerHTML = katex.renderToString(el.dataset.tex ?? '', { displayMode: el.dataset.display === '1', throwOnError: true, trust: false, strict: 'ignore', maxExpand: 200 }); el.dataset.done = '1' }
    catch { el.dataset.done = '1' /* leave the escaped source visible */ }
  })
}
onMounted(hydrateMath)
watch(() => props.source, () => { if (!props.streaming) hydrateMath() })
watch(() => props.streaming, (s, was) => { if (was && !s) hydrateMath() })
</script>
<template>
  <div ref="root" class="prose-lite text-sm">
    <template v-for="s in segments" :key="s.key">
      <div v-if="s.kind === 'html'" v-html="s.html" />
      <MermaidView v-else-if="s.kind === 'mermaid'" :code="s.code" :streaming="streaming" />
      <CodeView v-else :code="s.code" :lang="s.lang" :streaming="streaming" />
    </template>
    <span v-if="streaming" class="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" aria-hidden="true" data-testid="stream-caret" />
  </div>
</template>
