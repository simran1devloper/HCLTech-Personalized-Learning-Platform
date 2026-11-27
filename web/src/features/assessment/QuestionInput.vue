<script setup lang="ts">
import { computed, defineAsyncComponent, useId } from 'vue'
import Textarea from '@/components/ui/Textarea.vue'
import type { AssessmentQuestion } from '@/types/api'

const CodeEditor = defineAsyncComponent(() => import('./CodeEditor.vue'))
const props = defineProps<{ q: AssessmentQuestion; modelValue: unknown; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: unknown] }>()
const gid = useId()
const kind = computed(() => (['multiple_choice', 'true_false'].includes(props.q.type) ? 'single' : props.q.type === 'multiple_select' ? 'multi' : ['coding', 'debugging'].includes(props.q.type) ? 'code' : 'text'))
const picked = computed(() => (Array.isArray(props.modelValue) ? (props.modelValue as number[]) : []))
function toggle(i: number, on: boolean) {
  const next = on ? [...picked.value, i] : picked.value.filter((x) => x !== i)
  emit('update:modelValue', next.sort((a, b) => a - b))
}
const placeholders: Record<string, string> = { scenario: 'Describe what you would do and why…', system_design: 'Outline components, data flow, scaling and trade-offs…', practical: 'Describe or paste your approach…', short_answer: 'Your answer…' }
</script>
<template>
  <fieldset :disabled="disabled" class="space-y-3">
    <legend class="text-base font-medium leading-snug">{{ q.prompt }}</legend>
    <p v-if="q.type === 'multiple_select'" class="text-xs text-muted-foreground">Select all that apply.</p>
    <div v-if="kind === 'single'" role="radiogroup" :aria-label="q.prompt" class="space-y-2">
      <label v-for="(o, i) in q.options" :key="i" class="flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm has-[:checked]:border-primary has-[:checked]:bg-accent has-[:disabled]:cursor-default">
        <input type="radio" :name="gid" :value="i" :checked="modelValue === i" class="mt-0.5 accent-[var(--primary)]" @change="emit('update:modelValue', i)" />{{ o }}</label>
    </div>
    <div v-else-if="kind === 'multi'" class="space-y-2">
      <label v-for="(o, i) in q.options" :key="i" class="flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm has-[:checked]:border-primary has-[:checked]:bg-accent">
        <input type="checkbox" :checked="picked.includes(i)" class="mt-0.5 accent-[var(--primary)]" @change="toggle(i, ($event.target as HTMLInputElement).checked)" />{{ o }}</label>
    </div>
    <CodeEditor v-else-if="kind === 'code'" :model-value="(modelValue as string) ?? ''" :label="`Code answer: ${q.prompt}`" @update:model-value="emit('update:modelValue', $event)" />
    <Textarea v-else :model-value="(modelValue as string) ?? ''" :rows="q.type === 'short_answer' ? 3 : 8" :placeholder="placeholders[q.type]" :aria-label="q.prompt" @update:model-value="emit('update:modelValue', $event)" />
    <p v-if="kind === 'code'" class="text-xs text-muted-foreground">Code is graded by the backend in an isolated runner; if no runner is configured the item is marked “needs review” and not scored.</p>
  </fieldset>
</template>
