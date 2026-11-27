<script setup lang="ts">
import { onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useUiStore } from '@/stores/ui'

/** Monaco, loaded on demand (own chunk). Only the core editor + Python tokenizer are bundled. */
const model = defineModel<string>({ default: '' })
const props = defineProps<{ label: string; language?: string }>()
const host = shallowRef<HTMLDivElement>(); const loading = shallowRef(true)
let editor: any, monaco: any
const ui = useUiStore()

onMounted(async () => {
  const [m, workerMod] = await Promise.all([
    import('monaco-editor/editor/editor.api'),
    import('monaco-editor/editor/editor.worker?worker'),
    import('monaco-editor/languages/definitions/python/register'),
  ])
  monaco = m
  ;(self as any).MonacoEnvironment = { getWorker: () => new (workerMod as any).default() }
  if (!host.value) return
  editor = monaco.editor.create(host.value, {
    value: model.value, language: props.language ?? 'python', automaticLayout: true, minimap: { enabled: false }, fontSize: 14,
    scrollBeyondLastLine: false, ariaLabel: props.label, tabSize: 4, theme: document.documentElement.classList.contains('dark') ? 'vs-dark' : 'vs',
    accessibilitySupport: 'on', padding: { top: 8 },
  })
  editor.onDidChangeModelContent(() => { const v = editor.getValue(); if (v !== model.value) model.value = v })
  loading.value = false
})
watch(model, (v) => { if (editor && v !== editor.getValue()) editor.setValue(v ?? '') })
watch(() => ui.theme, () => requestAnimationFrame(() => monaco?.editor.setTheme(document.documentElement.classList.contains('dark') ? 'vs-dark' : 'vs')))
onBeforeUnmount(() => editor?.dispose())
</script>
<template>
  <div class="relative h-72 overflow-hidden rounded-md border">
    <Skeleton v-if="loading" class="absolute inset-0" />
    <div ref="host" class="size-full" />
    <p class="sr-only">Press Escape then Tab to leave the code editor.</p>
  </div>
</template>
