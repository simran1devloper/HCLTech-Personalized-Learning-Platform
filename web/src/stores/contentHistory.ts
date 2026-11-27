import { defineStore } from 'pinia'
import { ref } from 'vue'

const KEY = 'learnai-last-session'
/** Only a *pointer* to the session the learner last had open (so "Continue where you left off" works). The database remains the source of truth:
 *  the pointer is cleared the moment the backend says the session no longer exists, and it is dropped on sign-out. */
export const useContentHistoryStore = defineStore('contentHistory', () => {
  const read = () => { try { return JSON.parse(localStorage.getItem(KEY) ?? 'null') as { id: string; title: string } | null } catch { return null } }
  const last = ref<{ id: string; title: string } | null>(read())
  function remember(id: string, title: string) { last.value = { id, title }; localStorage.setItem(KEY, JSON.stringify(last.value)) }
  function forget(id?: string) { if (!id || last.value?.id === id) { last.value = null; localStorage.removeItem(KEY) } }
  return { last, remember, forget }
})
