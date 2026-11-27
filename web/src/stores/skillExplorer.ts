import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SkillStatus } from '@/types/api'

export type ExplorerView = 'graph' | 'list'

/** UI state of the Skill Explorer only. Server state (graph, details) lives in the TanStack Query cache, not here. */
export const useSkillExplorerStore = defineStore('skillExplorer', () => {
  const role = ref<string | null>(null)          // null -> follow the learner's latest career goal
  const selectedId = ref<string | null>(null)
  const search = ref('')
  const category = ref('')
  const statuses = ref<SkillStatus[]>([])
  const view = ref<ExplorerView>('graph')
  const focusMode = ref(false)                   // show only the selected skill's prerequisites and dependents

  function select(id: string | null) { selectedId.value = id; if (!id) focusMode.value = false }
  function setRole(slug: string | null) {
    if (slug === role.value) return
    role.value = slug; selectedId.value = null; focusMode.value = false
  }
  function toggleStatus(s: SkillStatus) {
    statuses.value = statuses.value.includes(s) ? statuses.value.filter((x) => x !== s) : [...statuses.value, s]
  }
  function resetFilters() { search.value = ''; category.value = ''; statuses.value = [] }
  return { role, selectedId, search, category, statuses, view, focusMode, select, setRole, toggleStatus, resetFilters }
})
