<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { Bell, GraduationCap, Menu, PanelLeftClose, PanelLeftOpen, Search, X } from 'lucide-vue-next'
import ThemeToggle from '@/components/ThemeToggle.vue'
import Button from '@/components/ui/Button.vue'
import DropdownMenu, { type MenuItem } from '@/components/ui/DropdownMenu.vue'
import Tooltip from '@/components/ui/Tooltip.vue'
import { NAV } from '@/composables/useNav'
import { analyticsApi } from '@/services/api'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const CommandPalette = defineAsyncComponent(() => import('@/features/shell/CommandPalette.vue'))
const auth = useAuthStore()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()

// Notifications = the backend's rule-based insights (real data). Shares the dashboard's cached query.
const { data: summary } = useQuery({ queryKey: ['analytics', 'summary'], queryFn: ({ signal }) => analyticsApi.summary({ signal }), enabled: computed(() => auth.isAuthenticated) })
const notifications = computed<MenuItem[]>(() => (summary.value?.insights ?? []).map((i) => ({ label: i.text, onSelect: () => router.push({ name: i.kind === 'next' ? 'roadmap' : 'analytics' }) })))

const initials = computed(() => (auth.user?.name ?? '?').split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase())
const userMenu = computed<MenuItem[]>(() => [
  { label: 'Analytics', onSelect: () => router.push({ name: 'analytics' }) },
  { label: 'Sign out', destructive: true, separatorBefore: true, onSelect: signOut },
])
function signOut() { auth.logout(); qc.clear(); toast.success('Signed out'); router.push({ name: 'login' }) }

function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); ui.paletteOpen = !ui.paletteOpen }
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
watch(() => route.fullPath, () => { ui.mobileNavOpen = false })
</script>

<template>
  <div class="min-h-screen">
    <a href="#main" class="sr-only z-[60] rounded-md bg-card px-3 py-2 focus:not-sr-only focus:fixed focus:left-2 focus:top-2">Skip to main content</a>

    <!-- Desktop sidebar -->
    <aside :class="['fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-[var(--border-soft)] bg-card shadow-[var(--shadow-1)] transition-[width] md:flex', ui.sidebarCollapsed ? 'w-16' : 'w-60']" aria-label="Primary">
      <div class="flex h-14 items-center gap-2 border-b px-4 font-semibold">
        <GraduationCap class="size-5 shrink-0 text-primary" aria-hidden="true" />
        <span v-if="!ui.sidebarCollapsed">LearnAI</span>
      </div>
      <nav class="flex-1 space-y-1 overflow-y-auto p-2" aria-label="Main navigation">
        <Tooltip v-for="n in NAV" :key="n.name" :text="n.label">
          <RouterLink :to="{ name: n.name }" :aria-label="ui.sidebarCollapsed ? n.label : undefined"
            class="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            active-class="nav-active font-medium" :exact-active-class="n.name === 'dashboard' ? '' : undefined">
            <component :is="n.icon" class="size-4 shrink-0" aria-hidden="true" />
            <span v-if="!ui.sidebarCollapsed">{{ n.label }}</span>
          </RouterLink>
        </Tooltip>
      </nav>
      <div class="border-t p-2">
        <Button variant="ghost" class="w-full justify-start" :aria-label="ui.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'" :aria-expanded="!ui.sidebarCollapsed" @click="ui.sidebarCollapsed = !ui.sidebarCollapsed">
          <component :is="ui.sidebarCollapsed ? PanelLeftOpen : PanelLeftClose" aria-hidden="true" />
          <span v-if="!ui.sidebarCollapsed">Collapse</span>
        </Button>
      </div>
    </aside>

    <!-- Mobile drawer -->
    <div v-if="ui.mobileNavOpen" class="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation menu">
      <div class="absolute inset-0 bg-slate-900/40" @click="ui.mobileNavOpen = false" />
      <nav class="absolute inset-y-0 left-0 w-72 space-y-1 border-r bg-card p-3" aria-label="Mobile navigation">
        <div class="mb-2 flex items-center justify-between px-2"><span class="font-semibold">LearnAI</span>
          <Button variant="ghost" size="icon" aria-label="Close menu" @click="ui.mobileNavOpen = false"><X /></Button></div>
        <RouterLink v-for="n in NAV" :key="n.name" :to="{ name: n.name }" class="flex min-h-11 items-center gap-3 rounded-md px-3 text-sm hover:bg-accent" active-class="nav-active font-medium">
          <component :is="n.icon" class="size-4" aria-hidden="true" />{{ n.label }}
        </RouterLink>
      </nav>
    </div>

    <div :class="['transition-[padding]', ui.sidebarCollapsed ? 'md:pl-16' : 'md:pl-60']">
      <header class="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-[var(--border-soft)] bg-card/95 px-3 shadow-[var(--shadow-1)] md:px-6">
        <Button variant="ghost" size="icon" class="md:hidden" aria-label="Open menu" @click="ui.mobileNavOpen = true"><Menu /></Button>
        <button type="button" class="flex h-9 flex-1 items-center gap-2 field px-3 text-sm text-muted-foreground hover:bg-accent md:max-w-md" @click="ui.paletteOpen = true">
          <Search class="size-4" aria-hidden="true" /><span class="flex-1 text-left">Search skills, pages, actions…</span>
          <kbd class="hidden rounded border px-1.5 text-[10px] sm:inline">Ctrl K</kbd>
        </button>
        <div class="ml-auto flex items-center gap-1">
          <ThemeToggle />
          <DropdownMenu :items="notifications.length ? notifications : [{ label: 'No notifications', onSelect: () => {} }]" heading="Insights from your data">
            <Button variant="ghost" size="icon" class="relative" :aria-label="`Notifications (${notifications.length})`">
              <Bell /><span v-if="notifications.length" class="absolute right-1.5 top-1.5 size-2 rounded-full bg-primary" aria-hidden="true" />
            </Button>
          </DropdownMenu>
          <DropdownMenu :items="userMenu" :heading="auth.user?.email">
            <Button variant="ghost" size="icon"><span class="sr-only">Account menu</span><span aria-hidden="true" class="grid size-7 place-items-center rounded-full btn-gradient text-xs font-semibold">{{ initials }}</span></Button>
          </DropdownMenu>
        </div>
      </header>
      <main id="main" tabindex="-1" class="mx-auto w-full max-w-7xl p-4 md:p-6"><slot /></main>
    </div>
    <CommandPalette v-if="ui.paletteOpen" />
  </div>
</template>
