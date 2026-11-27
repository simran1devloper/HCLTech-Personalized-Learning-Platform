<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { Cog, LogOut, Network, Palette, RefreshCw } from 'lucide-vue-next'
import { DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'
import { NAV } from '@/composables/useNav'
import { skillsApi, roadmapApi } from '@/services/api'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { Component } from 'vue'

interface Cmd { id: string; group: string; label: string; icon: Component; hint?: string; run: () => void | Promise<void> }
const ui = useUiStore(); const auth = useAuthStore(); const router = useRouter(); const qc = useQueryClient()
const query = ref(''); const active = ref(0); const input = ref<HTMLInputElement>()

const { data: skills } = useQuery({ queryKey: ['skills', 'all'], queryFn: ({ signal }) => skillsApi.list({}, { signal }), staleTime: 5 * 60_000 })

async function replanLatest() {
  const { items } = await roadmapApi.list()
  if (!items.length) { toast.info('No roadmap yet — generate one first.'); router.push({ name: 'roadmap' }); return }
  const r = await roadmapApi.replan(items[0].roadmap_id)
  qc.invalidateQueries({ queryKey: ['roadmap'] })
  toast.success(r.changed ? 'Roadmap re-planned' : 'No changes needed')
}
const base = computed<Cmd[]>(() => [
  ...NAV.map((n) => ({ id: 'nav-' + n.name, group: 'Go to', label: n.label, icon: n.icon, hint: n.keywords, run: () => { router.push({ name: n.name }) } })),
  { id: 'a-theme', group: 'Actions', label: ui.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme', icon: Palette, hint: 'theme dark light appearance', run: () => ui.toggleTheme() },
  { id: 'a-replan', group: 'Actions', label: 'Re-plan my latest roadmap', icon: RefreshCw, hint: 'update roadmap', run: replanLatest },
  { id: 'a-signout', group: 'Actions', label: 'Sign out', icon: LogOut, run: () => { auth.logout(); qc.clear(); router.push({ name: 'login' }) } },
])
const skillCmds = computed<Cmd[]>(() => (skills.value?.items ?? []).map((s) => ({
  id: 'skill-' + s.skill_id, group: 'Skills', label: s.name, icon: Network, hint: `${s.category} ${s.aliases.join(' ')}`,
  run: () => { router.push({ name: 'skills', query: { skill: s.skill_id } }) },
})))
const results = computed(() => {
  const q = query.value.trim().toLowerCase() // synchronous: Enter must act on what is on screen
  const all = [...base.value, ...(q ? skillCmds.value : [])]
  const hit = q ? all.filter((c) => (c.label + ' ' + (c.hint ?? '')).toLowerCase().includes(q)) : all
  return hit.slice(0, 30)
})
watch(results, () => (active.value = 0))
watch(input, (el) => el && nextTick(() => el.focus()), { immediate: true })

async function run(c: Cmd) {
  ui.paletteOpen = false
  try { await c.run() } catch (e) { toast.error((e as Error).message) }
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') { e.preventDefault(); active.value = (active.value + 1) % Math.max(1, results.value.length) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); active.value = (active.value - 1 + results.value.length) % Math.max(1, results.value.length) }
  else if (e.key === 'Enter' && results.value[active.value]) { e.preventDefault(); run(results.value[active.value]) }
}
</script>

<template>
  <DialogRoot :open="ui.paletteOpen" @update:open="ui.paletteOpen = $event">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-black/50" />
      <DialogContent class="fixed left-1/2 top-[14vh] z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden surface surface-hero" @open-auto-focus.prevent="input?.focus()">
        <DialogTitle class="sr-only">Command palette</DialogTitle>
        <DialogDescription class="sr-only">Type to search pages, skills and actions. Use arrow keys and Enter.</DialogDescription>
        <input ref="input" v-model="query" role="combobox" aria-expanded="true" aria-controls="cmd-list" aria-label="Search commands"
          :aria-activedescendant="results[active] ? 'cmd-' + results[active].id : undefined" placeholder="Type a command or search skills…"
          class="h-12 w-full border-b bg-transparent px-4 text-sm outline-none" @keydown="onKey" />
        <ul id="cmd-list" role="listbox" aria-label="Results" class="max-h-80 overflow-y-auto p-1">
          <li v-if="!results.length" class="px-3 py-6 text-center text-sm text-muted-foreground">No results</li>
          <template v-for="(c, i) in results" :key="c.id">
            <li v-if="i === 0 || results[i - 1].group !== c.group" role="presentation" class="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">{{ c.group }}</li>
            <li :id="'cmd-' + c.id" role="option" :aria-selected="i === active" :class="['flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm', i === active && 'bg-accent text-accent-foreground']" @mousemove="active = i" @click="run(c)">
              <component :is="c.icon" class="size-4" aria-hidden="true" />{{ c.label }}
            </li>
          </template>
        </ul>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
