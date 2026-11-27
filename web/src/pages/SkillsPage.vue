<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useFullscreen } from '@vueuse/core'
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { Briefcase, FilterX, LayoutList, Network, Search, Target } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Input from '@/components/ui/Input.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import CareerOverview from '@/features/skills/CareerOverview.vue'
import CareerSummaryPanel from '@/features/skills/CareerSummaryPanel.vue'
import SkillDetailPanel from '@/features/skills/SkillDetailPanel.vue'
import SkillGraphView from '@/features/skills/SkillGraphView.vue'
import SkillListView from '@/features/skills/SkillListView.vue'
import StatusLegend from '@/features/skills/StatusLegend.vue'
import { bestMatch, buildIndex, filtersActive, highlightFor, matchesQuery, searchMatches, visibleSubgraph } from '@/features/skills/graph'
import { useDebounced } from '@/composables/useDebounced'
import { useCareerGraph, useGoals, useRoles, useSkillDetail } from '@/composables/queries'
import { ALL_SKILLS, careerApi } from '@/services/api'
import { useSkillExplorerStore } from '@/stores/skillExplorer'
import type { SkillStatus } from '@/types/api'
import { categoryLabel } from '@/features/skills/status'

const route = useRoute(); const router = useRouter(); const qc = useQueryClient()
const ex = useSkillExplorerStore()
const rolesQ = useRoles(); const goalsQ = useGoals()

// ---- which career is being explored: ?role= (shareable) > this session's choice > the learner's latest goal
const goalRole = computed(() => goalsQ.data.value?.at(-1)?.role_slug)
// No goal yet -> the career-agnostic view of every skill (still useful, and what a deep link to any skill needs). Wait for the goal before fetching anything.
const role = computed(() => ex.role ?? (goalsQ.isPending.value ? undefined : goalRole.value ?? ALL_SKILLS))
const careerChosen = computed(() => !!role.value && role.value !== ALL_SKILLS)
if (typeof route.query.role === 'string') ex.setRole(route.query.role)
if (typeof route.query.skill === 'string') ex.select(route.query.skill)
const graphQ = useCareerGraph(role)
const graph = computed(() => graphQ.data.value)

// ---- derived graph data: computed once per payload / filter change, never per frame
const index = computed(() => buildIndex(graph.value?.nodes ?? [], graph.value?.edges ?? []))
const dsearch = useDebounced(computed({ get: () => ex.search, set: (v) => (ex.search = v) }), 150)
const filters = computed(() => ({ q: dsearch.value, category: ex.category, statuses: ex.statuses }))
const visible = computed(() => visibleSubgraph(graph.value?.nodes ?? [], graph.value?.edges ?? [], index.value, filters.value, ex.focusMode ? ex.selectedId : null))
const highlight = computed(() => highlightFor(index.value, ex.selectedId))
const searched = computed(() => searchMatches(visible.value.nodes, dsearch.value))
const matches = computed(() => (searched.value?.size ? searched.value : null))        // a search that matches nothing dims nothing (and says so below)
const noMatch = computed(() => !!searched.value && !searched.value.size)
const listNodes = computed(() => visible.value.nodes.filter((n) => matchesQuery(n, dsearch.value)))
const counts = computed(() => { const c: Partial<Record<SkillStatus, number>> = {}; for (const n of graph.value?.nodes ?? []) c[n.status] = (c[n.status] ?? 0) + 1; return c })
const selectedNode = computed(() => (ex.selectedId ? index.value.byId.get(ex.selectedId) : undefined))
// A selection missing from the loaded career: a deep link (e.g. from the command palette) to a skill outside the learner's career first retries in the
// all-skills view; anything still unknown is dropped instead of showing stale details.
let triedAll = false
watch(() => [graph.value, ex.selectedId, graphQ.isPlaceholderData.value] as const, ([g, id, placeholder]) => {
  if (!g || !id || placeholder || index.value.byId.has(id)) return   // while the next career loads, the previous graph is only a placeholder
  const bySlug = g.nodes.find((n) => n.slug === id)                    // readable links: ?skill=fastapi works as well as the skill id
  if (bySlug) { select(bySlug.skill_id); return }
  if (!triedAll && role.value !== ALL_SKILLS && typeof route.query.role !== 'string') { triedAll = true; ex.setRole(ALL_SKILLS); ex.select(id); sync(); return }
  select(null)
})

const detailQ = useSkillDetail(computed(() => selectedNode.value?.skill_id), role)

// ---- selection <-> URL
function sync() { if (route.name !== 'skills') return; router.replace({ query: { ...(ex.role ? { role: ex.role } : {}), ...(ex.selectedId ? { skill: ex.selectedId } : {}) } }) }
function select(id: string | null) { ex.select(id); sync() }
function changeRole(slug: string) { ex.setRole(slug === goalRole.value ? null : slug); sync() }
watch(() => route.query.skill, (v) => { if (route.name !== 'skills') return; const id = typeof v === 'string' ? v : null; if (id !== ex.selectedId) ex.select(id) })

// ---- search: filters/dims while typing; Enter jumps to the best match
const canvas = ref<InstanceType<typeof SkillGraphView> | null>(null)
function jump() {
  const hit = bestMatch(visible.value.nodes, ex.search); if (!hit) return
  select(hit.skill_id)
  if (ex.view === 'graph') setTimeout(() => canvas.value?.focusNode(hit.skill_id), 0)
}
function pick(id: string) { select(id); setTimeout(() => canvas.value?.focusNode(id), 0) }

// ---- fullscreen (native when available, a fixed overlay otherwise)
const stage = ref<HTMLElement | null>(null)
const { isFullscreen, isSupported, toggle: toggleNative } = useFullscreen(stage)
const pseudo = ref(false)
const full = computed(() => isFullscreen.value || pseudo.value)
function toggleFull() { isSupported.value ? toggleNative() : (pseudo.value = !pseudo.value) }

// ---- actions: Start Learning goes to the existing Diagnostic Study with the skill and career context
const ctx = (id: string) => { const n = index.value.byId.get(id); return { skill: id, topic: n?.name ?? '', ...(careerChosen.value ? { career: role.value } : {}) } }
const start = (id: string) => router.push({ name: 'study', query: { ...ctx(id), intent: 'learn' } })
const revise = (id: string) => router.push({ name: 'study', query: { ...ctx(id), intent: 'revise' } })
const assess = (id: string) => router.push({ name: 'assessments', query: { skill: index.value.byId.get(id)?.name ?? id } })
function act(id: string, action: string) { action === 'revise' ? revise(id) : action === 'take_assessment' ? assess(id) : start(id) }

const setGoal = useMutation({
  mutationFn: () => careerApi.createGoal({ role_slug: role.value!, hours_per_week: goalsQ.data.value?.at(-1)?.hours_per_week ?? 5 }),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['goals'] }); qc.invalidateQueries({ queryKey: ['analytics'] }); ex.setRole(null); sync(); toast.success('Career goal updated') },
  onError: (e: Error) => toast.error(e.message),
})
const roleTitle = computed(() => (careerChosen.value ? (graph.value?.role?.title ?? rolesQ.data.value?.find((r) => r.slug === role.value)?.title) : 'All skills'))
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h1 class="text-2xl font-semibold">Skill Explorer</h1>
        <p class="text-sm text-muted-foreground">Explore your career skills, understand prerequisites, and discover what to learn next.</p></div>
      <div class="flex items-center gap-2">
        <Button v-if="careerChosen && role !== goalRole" variant="outline" size="sm" :loading="setGoal.isPending.value" @click="setGoal.mutate()"><Target />Make this my goal</Button>
        <label class="flex items-center gap-2 surface px-3 py-1.5 text-sm"><Briefcase class="size-4 text-primary" aria-hidden="true" /><span class="sr-only">Target career</span>
          <select :value="role ?? ''" aria-label="Target career" class="bg-transparent pr-1 font-medium outline-none" @change="changeRole(($event.target as HTMLSelectElement).value)">
            <option v-if="!role" value="" disabled>Loading…</option>
            <option :value="ALL_SKILLS">All skills (no career)</option>
            <option v-for="r in rolesQ.data.value ?? []" :key="r.slug" :value="r.slug">{{ r.title }}</option>
          </select></label>
      </div>
    </div>

    <CareerOverview :metrics="graph?.metrics" :title="roleTitle" :loading="graphQ.isPending.value || !role" :career="careerChosen" />

    <ErrorState v-if="graphQ.isError.value && !graph" :message="(graphQ.error.value as Error).message" @retry="graphQ.refetch()" />
    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative w-full sm:w-72"><Search class="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden="true" />
          <Input v-model="ex.search" class="pl-8" placeholder="Search for a skill…" aria-label="Search skills" @keydown.enter.prevent="jump" /></div>
        <select v-model="ex.category" aria-label="Filter by category" class="h-9 field px-2 text-sm">
          <option value="">All categories</option><option v-for="c in graph?.categories ?? []" :key="c" :value="c">{{ categoryLabel(c) }}</option>
        </select>
        <Button v-if="filtersActive(filters) || ex.statuses.length" variant="ghost" size="sm" @click="ex.resetFilters()"><FilterX />Clear filters</Button>
        <div class="ml-auto inline-flex rounded-lg border bg-card p-0.5" role="group" aria-label="View">
          <button type="button" :aria-pressed="ex.view === 'graph'" :class="['inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm', ex.view === 'graph' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent']" @click="ex.view = 'graph'"><Network class="size-4" aria-hidden="true" />Graph</button>
          <button type="button" :aria-pressed="ex.view === 'list'" :class="['inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm', ex.view === 'list' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent']" @click="ex.view = 'list'"><LayoutList class="size-4" aria-hidden="true" />List</button>
        </div>
      </div>
      <StatusLegend :active="ex.statuses" :counts="counts" @toggle="ex.toggleStatus" />

      <div v-if="(graphQ.isPending.value && !graph) || !role" class="grid gap-4 lg:grid-cols-[1fr_22rem]"><Skeleton class="h-[34rem]" /><Skeleton class="h-[34rem]" /></div>
      <EmptyState v-else-if="graph && !graph.nodes.length" title="No skills for this career" description="The career has no skills mapped yet." />
      <div v-else-if="graph" ref="stage" :class="['grid gap-4 lg:grid-cols-[1fr_22rem]', full && 'bg-background p-3', pseudo && 'fixed inset-0 z-50 overflow-auto']">
        <Card :class="full ? 'overflow-hidden h-[calc(100vh-1.5rem)]' : 'overflow-hidden h-[34rem]'">
          <div class="flex h-full flex-col">
            <div class="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-xs text-muted-foreground">
              <ol v-if="ex.view === 'graph'" class="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Skill stages, left to right"><li v-for="(s, i) in graph.stages" :key="s" class="flex items-center gap-1"><span class="font-medium text-foreground">{{ i + 1 }}</span>{{ s }}</li></ol>
              <span v-else>Learning order</span>
              <span aria-live="polite" data-test="count">{{ visible.nodes.length }} of {{ graph.nodes.length }} skills<template v-if="visible.truncated"> · {{ visible.truncated }} hidden (refine the filters)</template><template v-if="ex.focusMode"> · focused</template><template v-if="noMatch"> · no skill matches “{{ dsearch.trim() }}”</template></span>
            </div>
            <div class="relative min-h-0 flex-1">
              <SkillGraphView v-if="ex.view === 'graph'" ref="canvas" :nodes="visible.nodes" :edges="visible.edges" :highlight="highlight" :matches="matches" :fullscreen="full" @select="select" @toggle-fullscreen="toggleFull" />
              <SkillListView v-else :nodes="listNodes" :selected-id="ex.selectedId" @select="select" />
              <div v-if="ex.view === 'graph' && !visible.nodes.length" class="absolute inset-0 grid place-items-center text-sm text-muted-foreground">No skills match these filters.</div>
            </div>
          </div>
        </Card>
        <div :class="['min-w-0', full && 'overflow-y-auto']">
          <SkillDetailPanel v-if="selectedNode" :node="selectedNode" :detail="detailQ.data.value" :pending="detailQ.isPending.value" :error="detailQ.isError.value ? (detailQ.error.value as Error).message : undefined"
            :focused="ex.focusMode" @close="select(null)" @select="pick" @start="start" @revise="revise" @assess="assess" @focus="ex.focusMode = $event" @retry="detailQ.refetch()" />
          <CareerSummaryPanel v-else :graph="graph" @select="pick" @act="act" />
        </div>
      </div>
    </template>
  </div>
</template>
