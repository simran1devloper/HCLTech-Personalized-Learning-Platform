import { defineStore } from 'pinia'
import { ref } from 'vue'

/** Autosave for in-progress answers. Keyed per user+assessment so drafts can never cross accounts.
 *  This is a recovery cache only; scoring and status always come from the backend. */
export const useAssessmentDraft = defineStore('assessmentDraft', () => {
  const answers = ref<Record<string, unknown>>({})
  const current = ref(0)
  const savedAt = ref<number | null>(null)
  let key = ''

  function keyFor(userId: string, assessmentId: string) { return `learnai-draft:${userId}:${assessmentId}` }
  function load(userId: string, assessmentId: string) {
    key = keyFor(userId, assessmentId)
    try {
      const raw = JSON.parse(localStorage.getItem(key) || 'null')
      answers.value = raw?.answers ?? {}
      current.value = raw?.current ?? 0
      savedAt.value = raw?.savedAt ?? null
    } catch { answers.value = {}; current.value = 0 }
  }
  function persist() {
    if (!key) return
    savedAt.value = Date.now()
    try { localStorage.setItem(key, JSON.stringify({ answers: answers.value, current: current.value, savedAt: savedAt.value })) } catch { /* quota */ }
  }
  function setAnswer(qid: string, value: unknown) {
    const empty = value === undefined || value === '' || (Array.isArray(value) && value.length === 0)
    const next = { ...answers.value }
    if (empty) delete next[qid]; else next[qid] = value
    answers.value = next
    persist()
  }
  /** Fill in answers the server already holds (autosaved on another device or before a refresh) without overwriting newer local choices. */
  function merge(server: Record<string, unknown>) {
    const next = { ...server, ...answers.value }
    if (Object.keys(next).length !== Object.keys(answers.value).length) { answers.value = next; persist() }
  }
  function goto(i: number) { current.value = i; persist() }
  function clear() { if (key) localStorage.removeItem(key); answers.value = {}; current.value = 0; savedAt.value = null }
  return { answers, current, savedAt, load, setAnswer, merge, goto, clear }
})
