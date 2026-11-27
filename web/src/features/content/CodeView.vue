<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { toast } from 'vue-sonner'
import { Check, Copy } from 'lucide-vue-next'
import { highlight } from './shiki'

const props = defineProps<{ code: string; lang: string; caption?: string; streaming?: boolean }>()
const html = ref<string | null>(null); const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined; let seq = 0
// Highlight after the text settles (200ms) so a streaming code block is not re-tokenised on every token.
watch(() => [props.code, props.lang], () => {
  clearTimeout(timer)
  timer = setTimeout(async () => { const n = ++seq; const h = await highlight(props.code, props.lang).catch(() => null); if (n === seq) html.value = h }, props.streaming ? 250 : 0)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
async function copy() {
  try { await navigator.clipboard.writeText(props.code); copied.value = true; toast.success('Code copied'); setTimeout(() => (copied.value = false), 1500) } catch { toast.error('Copy failed') }
}
</script>
<template>
  <figure class="not-prose my-3 overflow-hidden rounded-md border bg-muted/40">
    <div class="flex items-center justify-between border-b px-3 py-1.5 text-xs text-muted-foreground"><span>{{ lang }}</span>
      <button type="button" class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent" :aria-label="`Copy ${lang} code`" @click="copy"><component :is="copied ? Check : Copy" class="size-3.5" aria-hidden="true" />{{ copied ? 'Copied' : 'Copy' }}</button></div>
    <div v-if="html" v-html="html" />
    <pre v-else class="overflow-x-auto p-3 text-[0.85rem] leading-relaxed" tabindex="0"><code>{{ code }}</code></pre>
    <figcaption v-if="caption" class="border-t px-3 py-1.5 text-xs text-muted-foreground">{{ caption }}</figcaption>
  </figure>
</template>
