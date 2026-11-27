<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import CodeView from './CodeView.vue'

const props = defineProps<{ code: string; caption?: string; streaming?: boolean }>()
const svg = ref(''); const failed = ref(false); const ui = useUiStore()
let timer: ReturnType<typeof setTimeout> | undefined; let seq = 0; let counter = 0
async function render() {
  const n = ++seq
  try {
    const { default: mermaid } = await import('mermaid')
    const dark = document.documentElement.classList.contains('dark')
    // securityLevel 'strict' => labels are encoded and click/script directives are disabled.
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'default', fontFamily: 'inherit' })
    await mermaid.parse(props.code)
    const out = await mermaid.render(`mmd-${Date.now()}-${counter++}`, props.code)
    if (n === seq) { svg.value = out.svg; failed.value = false }
  } catch { if (n === seq) { failed.value = true; svg.value = '' } }
}
const schedule = () => { clearTimeout(timer); timer = setTimeout(render, props.streaming ? 600 : 0) }
watch(() => props.code, schedule, { immediate: true })
watch(() => ui.theme, schedule)
onBeforeUnmount(() => { clearTimeout(timer); seq++ })
</script>
<template>
  <figure class="my-3 surface p-3">
    <div v-if="svg" class="overflow-x-auto [&_svg]:mx-auto [&_svg]:max-w-full" role="img" :aria-label="caption || 'Diagram'" v-html="svg" />
    <p v-else-if="failed && streaming" class="py-6 text-center text-xs text-muted-foreground" role="status">Drawing diagram…</p>
    <template v-else-if="failed"><p class="mb-1 text-xs text-muted-foreground" role="status">This diagram could not be rendered; showing its source.</p><CodeView :code="code" lang="mermaid" /></template>
    <p v-else class="py-6 text-center text-xs text-muted-foreground" role="status">Rendering diagram…</p>
    <figcaption v-if="caption" class="mt-2 text-center text-xs text-muted-foreground">{{ caption }}</figcaption>
  </figure>
</template>
