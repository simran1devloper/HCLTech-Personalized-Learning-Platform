<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

/** KaTeX rendering (HTML + MathML for screen readers). If the expression cannot be rendered the LaTeX source is shown
 *  as text, never silently dropped and never as HTML. */
const props = defineProps<{ latex: string; display?: boolean; variables?: { symbol: string; meaning: string }[]; title?: string }>()
const html = ref(''); const failed = ref(false)
async function render() {
  try {
    const { default: katex } = await import('katex')
    await import('katex/dist/katex.min.css')
    html.value = katex.renderToString(props.latex, { displayMode: props.display !== false, throwOnError: true, trust: false, strict: 'error', maxExpand: 200, output: 'htmlAndMathml' })
    failed.value = false
  } catch { html.value = ''; failed.value = true }
}
onMounted(render)
watch(() => [props.latex, props.display], render)
</script>
<template>
  <figure class="my-3">
    <figcaption v-if="title" class="mb-1 text-xs font-medium text-muted-foreground">{{ title }}</figcaption>
    <div v-if="html" class="overflow-x-auto rounded-md bg-muted/40 px-3 py-2" v-html="html" />
    <pre v-else-if="failed" class="overflow-x-auto rounded-md border bg-muted/40 p-3 text-sm" role="note" aria-label="Equation could not be rendered; showing its LaTeX source"><code>{{ latex }}</code></pre>
    <p v-else class="py-2 text-xs text-muted-foreground" role="status">Rendering equation…</p>
    <dl v-if="variables?.length" class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm"><template v-for="v in variables" :key="v.symbol"><dt class="font-mono">{{ v.symbol }}</dt><dd class="text-muted-foreground">{{ v.meaning }}</dd></template></dl>
  </figure>
</template>
