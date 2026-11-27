<script setup lang="ts">
import { ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { CheckCircle2, ClipboardCheck, XCircle } from 'lucide-vue-next'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import QuestionInput from '@/features/assessment/QuestionInput.vue'
import { workspaceApi } from '@/services/api'
import type { KnowledgeCheck, KnowledgeCheckResult } from '@/types/api'

/** Section knowledge check from the saved question bank (weak areas first). Answers are graded on the server; nothing is generated, so it costs no model call. */
const props = defineProps<{ sessionId: string; sectionKey: string }>()
const round = ref(0); const check = ref<KnowledgeCheck | null>(null); const answers = ref<Record<string, unknown>>({}); const result = ref<KnowledgeCheckResult | null>(null)
const start = useMutation({ mutationFn: () => workspaceApi.startCheck(props.sessionId, props.sectionKey, { round: round.value, n: 3 }), onSuccess: (c) => { check.value = c; answers.value = {}; result.value = null } })
const submit = useMutation({ mutationFn: () => workspaceApi.submitCheck(check.value!.check_id!, answers.value), onSuccess: (r) => (result.value = r) })
const again = () => { round.value++; start.mutate() }
const byId = (id: string) => result.value?.results.find((r) => r.question_id === id)
const set = (id: string, v: unknown) => { const n = { ...answers.value }; if (v === undefined || (Array.isArray(v) && !v.length)) delete n[id]; else n[id] = v; answers.value = n }
</script>
<template>
  <Card class="space-y-3 p-4" aria-label="Knowledge check">
    <div class="flex flex-wrap items-center justify-between gap-2"><h3 class="flex items-center gap-2 text-sm font-semibold"><ClipboardCheck class="size-4 text-primary" aria-hidden="true" />Knowledge check</h3>
      <Button v-if="!check" size="sm" variant="outline" :loading="start.isPending.value" @click="start.mutate()">Test your understanding</Button></div>
    <p v-if="start.isError.value" role="alert" class="text-sm text-destructive">{{ (start.error.value as Error).message }}</p>
    <p v-if="check && !check.available" class="text-sm text-muted-foreground" role="status">{{ check.message }}</p>
    <template v-if="check?.available">
      <ol class="space-y-4"><li v-for="(q, i) in check.questions" :key="q.question_id">
        <p class="mb-1 text-xs text-muted-foreground">Question {{ i + 1 }} of {{ check.questions.length }}</p>
        <QuestionInput :q="q" :model-value="answers[q.question_id]" :disabled="!!result" @update:model-value="set(q.question_id, $event)" />
        <div v-if="byId(q.question_id)" class="mt-2 rounded-md border p-2 text-sm" :class="byId(q.question_id)!.correct ? 'border-success/50 bg-success/10' : 'border-destructive/40 bg-destructive/5'">
          <p class="flex items-center gap-1.5 font-medium"><component :is="byId(q.question_id)!.correct ? CheckCircle2 : XCircle" :class="['size-4', byId(q.question_id)!.correct ? 'text-success' : 'text-destructive']" aria-hidden="true" />{{ byId(q.question_id)!.correct ? 'Correct' : 'Not quite' }}</p>
          <p class="mt-1 text-muted-foreground">{{ byId(q.question_id)!.explanation }}</p></div></li></ol>
      <div v-if="result" class="flex flex-wrap items-center justify-between gap-2" role="status"><p class="text-sm"><Badge :tone="result.passed ? 'success' : 'warning'">{{ result.score }} / {{ result.total }}</Badge> {{ result.passed ? 'Nice work.' : 'Worth another look at this section.' }}</p>
        <Button size="sm" variant="outline" :loading="start.isPending.value" @click="again">Try another set</Button></div>
      <div v-else class="flex justify-end"><Button size="sm" :loading="submit.isPending.value" :disabled="Object.keys(answers).length < check.questions.length" @click="submit.mutate()">Check answers</Button></div>
    </template>
  </Card>
</template>
